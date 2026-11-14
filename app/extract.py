"""Reads one vendor response (email body + any attachments, any format) into structured facts.

Division of labour, on purpose:
  the model READS  - what did the vendor write, about which RFx line, in what unit and currency, and where
  the code COMPUTES - unit conversion, FX, discounts, totals, pass/fail (see normalize.py)
The model is told to copy numbers exactly as written and never to convert them.
"""
import io
import json
import mimetypes
import re
import time
from pathlib import Path

from . import db, llm

NATIVE = {".pdf": "application/pdf", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp"}


def xlsx_text(data: bytes):
    """Every non-empty cell with its address, so evidence can point at Sheet!Cell."""
    from openpyxl import load_workbook
    wb = load_workbook(io.BytesIO(data), data_only=True)
    out = []
    for ws in wb.worksheets:
        out.append(f"### Sheet '{ws.title}'")
        for row in ws.iter_rows():
            cells = [f"{c.coordinate}={c.value}" for c in row if c.value is not None]
            if cells:
                out.append(" | ".join(cells))
    return "\n".join(out)


def docx_text(data: bytes):
    from docx import Document
    d = Document(io.BytesIO(data))
    out = [p.text for p in d.paragraphs if p.text.strip()]
    for t in d.tables:
        for r in t.rows:
            out.append(" | ".join(c.text.strip() for c in r.cells))
    return "\n".join(out)


def pdf_text(data: bytes):
    from pypdf import PdfReader
    try:
        return "\n".join((p.extract_text() or "") for p in PdfReader(io.BytesIO(data)).pages)
    except Exception:
        return ""


def load_response(folder: Path):
    """-> (email dict, model parts, searchable source text, list of file infos)."""
    email = json.loads((folder / "email.json").read_text())
    parts = [llm.text_part(f"EMAIL from {email['from_name']} <{email['from']}> on {email['date']}\n"
                           f"Subject: {email['subject']}\n\n{email['body']}")]
    source = {"email body": email["body"]}
    files = []
    for name in email.get("attachments", []):
        p = folder / name
        if not p.exists():
            continue
        data, ext = p.read_bytes(), p.suffix.lower()
        info = {"name": name, "kind": "text"}
        if ext in (".xlsx", ".xlsm"):
            t = xlsx_text(data); parts.append(llm.text_part(f"[Attached spreadsheet: {name}]\n{t}")); source[name] = t
        elif ext == ".docx":
            t = docx_text(data); parts.append(llm.text_part(f"[Attached Word document: {name}]\n{t}")); source[name] = t
        elif ext in (".txt", ".csv", ".md"):
            t = data.decode("utf-8", "replace"); parts.append(llm.text_part(f"[Attached file: {name}]\n{t}")); source[name] = t
        elif ext in NATIVE:
            parts.append(llm.file_part(name, NATIVE[ext], data))
            if ext == ".pdf":
                source[name] = pdf_text(data); info["kind"] = "pdf"
            else:
                info["kind"] = "image"            # nothing to verify quotes against: handled as a lower-trust source
        else:
            parts.append(llm.text_part(f"[Attached file {name} is in a format that could not be read]"))
            info["kind"] = "unreadable"
        files.append(info)
    return email, parts, source, files


SYSTEM = """You read supplier quotations for a procurement team. A buyer will commit real money on what you extract, so accuracy and honesty about doubt matter more than completeness.

You are given the buyer's RFx (line items and questionnaire) and ONE vendor's response: an email and any attachments. Vendors ignore templates. Prices may be in a table, a paragraph, a footnote, a photo, or one line of text.

Rules:
1. Copy every number EXACTLY as the vendor wrote it. Never convert units or currency, never apply discounts, never multiply or divide. Other code does the arithmetic.
2. Report the price basis as written: the amount, the currency, and "per how many of what" (per piece, per 100 pieces, per 1000, per kg, per set). Put the vendor's own words for the unit in uom_text. Set unit_explicit=false if you had to infer the basis from context rather than read it next to the price or in a header/note that clearly covers it.
3. Map each quoted item to the RFx line it answers (line_id). Vendors use their own codes, order, inches instead of mm, abbreviations. Match on dimensions, ply and type. If you cannot map an item with confidence, set line_id to null and explain in note. Never force a match.
4. One entry per RFx line that the vendor priced, even when the vendor gave one rate for a whole group ("Rs 42/kg for the 5-ply"): expand it to each line it covers, with price_reference="group_rate". If it is unclear whether a group statement covers a particular line (for example whether "the 3-ply" includes 3-ply mailers, pads or partitions, or only standard boxes), still create the entry under your best reading, but set match_confidence to "low" for that line, say why in note, and record the doubt in ambiguities. The same applies to lines swept up by "rest same as last year".
5. If the vendor refers to prices without stating them ("rest same as last year"), create entries for the lines covered with amount=null and price_reference="same_as_last_year".
6. Lines the vendor did not quote, or declined, go in not_quoted with the reason if given. Do not invent a price for them.
7. evidence must be a short VERBATIM snippet copied from the source containing the price (so it can be searched for). location says where: file name plus sheet/cell, page, paragraph, or "email body".
8. read_confidence: "high" only if the characters are unambiguous. Use "medium" or "low" for photos, blur, glare, handwriting, or digits that could be read two ways, and say what the alternative reading is in note.
9. If the vendor states a line total or extended value, copy it into stated_line_total, and the quantity it used into stated_qty.
10. spec_deviation is ONLY for a different physical product: other board grade, GSM, ply, dimensions or print than the RFx line asks for. A different unit, currency, price basis or commercial term is NOT a spec deviation; leave spec_deviation null for those.
11. Commercial terms (freight, taxes, payment, validity, lead time, discounts or rebates and their conditions, one-time charges) often hide in footnotes, notes and handwriting. Find them. A discount with a condition must carry the condition.
12. Questionnaire: answer only from what the vendor wrote or attached. If a question is not addressed, omit it. Do not guess.
13. For each attachment that is a supporting document (certificate, test report), summarise it and capture any validity or expiry date.
14. List in ambiguities anything a careful buyer would want to ask the vendor before trusting the numbers.

Return ONE JSON object, no prose, with exactly this shape:
{
 "vendor_name": str, "quote_ref": str|null, "quote_date": "YYYY-MM-DD"|null,
 "items": [{"line_id": str|null, "vendor_description": str, "amount": number|null, "currency": "INR"|"USD"|"EUR"|other ISO code,
            "per_qty": number, "uom": "piece"|"kg"|"set"|"other", "uom_text": str, "unit_explicit": bool,
            "price_reference": "explicit"|"group_rate"|"same_as_last_year",
            "stated_line_total": number|null, "stated_qty": number|null, "spec_deviation": str|null,
            "evidence": str, "location": str, "source_file": str,
            "read_confidence": "high"|"medium"|"low", "match_confidence": "high"|"medium"|"low", "note": str|null}],
 "not_quoted": [{"line_id": str, "reason": str|null, "evidence": str|null}],
 "terms": {"freight": {"status": "included"|"extra"|"unclear", "detail": str|null, "evidence": str|null},
           "taxes": str|null, "payment_terms": str|null, "validity": str|null, "lead_time_days": number|null,
           "discounts": [{"pct": number, "condition": str|null, "applies_to": str, "evidence": str}],
           "one_time_charges": [{"description": str, "amount": number, "currency": str, "evidence": str}],
           "other": [str]},
 "questionnaire": [{"question_id": str, "answer_text": str, "value": "yes"|"no"|number|null,
                    "valid_until": "YYYY-MM-DD"|null, "evidence": str, "source_file": str}],
 "documents": [{"filename": str, "doc_type": "iso_9001_certificate"|"test_report"|"quotation"|"other", "summary": str,
                "valid_until": "YYYY-MM-DD"|null, "key_facts": [str]}],
 "ambiguities": [{"about": str, "detail": str, "question_for_vendor": str}]
}
"uom": use "piece" for box/nos/each/pc/EA/sheet/pad, "set" for sets, "kg" for weight. source_file is the attachment name, or "email body"."""


def rfx_brief(rfx):
    lines = [f"{l['id']} | {l['description']} | {l['board_spec']} | print: {l['print']} | unit: {l['uom']} | annual qty: {l['annual_qty']}"
             for l in rfx["lines"]]
    qs = [f"{q['id']} | {q['text']}" for q in rfx.get("questionnaire", [])]
    ly = rfx.get("last_year") or {}
    return (f"RFx {rfx.get('id')}: {rfx.get('title')}\nRFx currency: {rfx.get('currency', 'INR')}. "
            f"Requested basis: price per RFx unit, delivered, exclusive of GST.\n"
            f"Last year's contract ({ly.get('contract_ref', 'n/a')}) was held by vendor {ly.get('vendor_id', 'n/a')}; "
            f"its prices are on file with the buyer, you do not need them.\n\n"
            "LINE ITEMS (id | description | board | print | unit | annual qty)\n" + "\n".join(lines) +
            "\n\nQUESTIONNAIRE (id | question)\n" + "\n".join(qs))


def norm(s):
    return re.sub(r"[^a-z0-9.]", "", (s or "").lower())


def evidence_found(evidence, source: dict, source_file=None):
    """True/False for text sources; None when the source is an image we cannot search."""
    e = norm(evidence)
    if len(e) < 3:
        return False
    texts = [source[source_file]] if source_file in source else list(source.values())
    if any(e in norm(t) for t in texts):
        return True
    # tolerate the model stitching table cells together: every numeric token must be present
    nums = re.findall(r"\d[\d,]*\.?\d*", evidence or "")
    hay = " ".join(texts).replace(",", "")
    return bool(nums) and all(n.replace(",", "") in hay for n in nums)


def extract_vendor(vendor_id, rfx, force=False):
    row = db.rows("SELECT * FROM responses WHERE vendor_id=?", (vendor_id,))
    if not row:
        raise ValueError(f"No response on file for {vendor_id}")
    row = row[0]
    if row["status"] == "extracted" and not force:
        return json.loads(row["extraction"])
    folder = Path(row["dir"])
    try:
        email, parts, source, files = load_response(folder)
        vendor = next((v for v in rfx["vendors"] if v["id"] == vendor_id), {"name": email["from_name"]})
        prompt = [llm.text_part(rfx_brief(rfx) + f"\n\nThe response below is from vendor {vendor_id} ({vendor['name']}).\n"
                                "--- VENDOR RESPONSE STARTS ---")] + parts
        data = llm.read_json(SYSTEM, prompt)
        data = clean(data, rfx, source, files)
        db.run("UPDATE responses SET status='extracted', extraction=?, source_text=?, model=?, error=NULL, extracted_at=? WHERE vendor_id=?",
               (json.dumps(data, ensure_ascii=False), json.dumps(source, ensure_ascii=False), llm.model_name(), time.time(), vendor_id))
        db.audit("extracted", f"{vendor_id} via {llm.model_name()}: {len(data['items'])} priced items")
        return data
    except Exception as e:
        db.run("UPDATE responses SET status='error', error=? WHERE vendor_id=?", (f"{type(e).__name__}: {e}", vendor_id))
        raise


def _num(x):
    if isinstance(x, (int, float)):
        return float(x)
    if isinstance(x, str):
        try:
            return float(x.replace(",", "").replace("₹", "").replace("$", "").strip())
        except ValueError:
            return None
    return None


def clean(data, rfx, source, files):
    """Validate the model's output against things we can check without a model."""
    ids = {l["id"] for l in rfx["lines"]}
    kinds = {f["name"]: f["kind"] for f in files}
    items = []
    for it in data.get("items") or []:
        if not isinstance(it, dict):
            continue
        it["line_id"] = it.get("line_id") if it.get("line_id") in ids else None
        it["amount"] = _num(it.get("amount"))
        it["per_qty"] = _num(it.get("per_qty")) or 1.0
        it["stated_line_total"] = _num(it.get("stated_line_total"))
        it["stated_qty"] = _num(it.get("stated_qty"))
        it["currency"] = (it.get("currency") or rfx.get("currency", "INR")).upper()
        it["uom"] = (it.get("uom") or "other").lower()
        src = it.get("source_file") or ""
        it["source_kind"] = "image" if kinds.get(src) == "image" else "text"
        if it["source_kind"] == "image":
            it["evidence_verified"] = None
        else:
            ok = evidence_found(it.get("evidence"), source, src)
            if ok and it["amount"] is not None:          # the number itself must be in the document, not just nearby words
                a = it["amount"]
                hay = " ".join(source.values()).replace(",", "")
                ok = any(v in hay for v in {f"{a:.2f}", f"{a:g}", f"{a:.4f}", f"{a:.0f}" if a == int(a) else f"{a:.3f}"})
            it["evidence_verified"] = ok
        items.append(it)
    data["items"] = items
    data["not_quoted"] = [n for n in (data.get("not_quoted") or []) if isinstance(n, dict) and n.get("line_id") in ids]
    t = data.get("terms") or {}
    t.setdefault("freight", {"status": "unclear", "detail": None, "evidence": None})
    t["discounts"] = [d for d in (t.get("discounts") or []) if _num(d.get("pct"))]
    for d in t["discounts"]:
        d["pct"] = _num(d["pct"])
    t.setdefault("one_time_charges", []); t.setdefault("other", [])
    data["terms"] = t
    qids = {q["id"] for q in rfx.get("questionnaire", [])}
    qa = []
    for a in data.get("questionnaire") or []:
        if isinstance(a, dict) and a.get("question_id") in qids:
            src = a.get("source_file") or ""
            a["evidence_verified"] = None if kinds.get(src) == "image" else evidence_found(a.get("evidence"), source, src)
            qa.append(a)
    data["questionnaire"] = qa
    data["documents"] = [d for d in (data.get("documents") or []) if isinstance(d, dict)]
    data["ambiguities"] = [a for a in (data.get("ambiguities") or []) if isinstance(a, dict)]
    data["files"] = files
    return data


def guess_mime(name):
    return mimetypes.guess_type(name)[0] or "application/octet-stream"
