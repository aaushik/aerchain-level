"""RFx co-pilot: the buyer talks, the model edits a structured draft through tools.

The draft is data, not prose: lines with dimensions and units, questions with pass rules, terms. That structure is
what makes the later comparison possible, so the co-pilot's real job is to pin down units and pass criteria up front.
"""
import json

from . import db, llm
from .spec import est_weight_g

EMPTY = {"id": "RFQ-DRAFT", "title": "", "scope": "", "currency": "INR", "due_date": None, "award_date": None, "status": "draft",
         "buyer": {"company": "Saffron Basket Retail Pvt Ltd", "name": "Meera Iyer", "role": "Category Buyer, Packaging",
                   "email": "meera.iyer@saffronbasket.example"},
         "terms": [], "lines": [], "questionnaire": [], "vendors": [], "last_year": None, "fx": {"USD": 88.5}}

LINE_PROPS = {
    "description": {"type": "string"}, "kind": {"type": "string", "enum": ["rsc", "mailer", "partition", "pad", "roll", "other"]},
    "category": {"type": "string"}, "ply": {"type": "integer"}, "length_mm": {"type": "number"}, "width_mm": {"type": "number"},
    "height_mm": {"type": "number"}, "board_spec": {"type": "string"}, "print": {"type": "string"},
    "uom": {"type": "string", "enum": ["piece", "set", "kg"]}, "annual_qty": {"type": "number"}}

TOOLS = [
    {"name": "get_last_year_contract", "description": "Look up last year's contract for this category in the contract repository: "
     "the line items, quantities, prices, incumbent vendor, questionnaire and terms that were used.", "schema": {"type": "object", "properties": {}}},
    {"name": "get_vendor_master", "description": "List approved vendors for this category.", "schema": {"type": "object", "properties": {}}},
    {"name": "set_header", "description": "Set RFx title, scope, due date and award date (YYYY-MM-DD).",
     "schema": {"type": "object", "properties": {"title": {"type": "string"}, "scope": {"type": "string"}, "due_date": {"type": "string"},
                                                  "award_date": {"type": "string"}}}},
    {"name": "import_last_year_lines", "description": "Copy line items from last year's contract into the draft, exactly, optionally "
     "changing all quantities by a percentage. Use this instead of retyping lines.",
     "schema": {"type": "object", "properties": {"qty_change_pct": {"type": "number"},
                                                  "categories": {"type": "array", "items": {"type": "string"}, "description": "Only these categories; omit for all"}}}},
    {"name": "add_lines", "description": "Add new line items to the draft.",
     "schema": {"type": "object", "properties": {"lines": {"type": "array", "items": {"type": "object", "properties": LINE_PROPS,
                                                                                  "required": ["description", "kind", "uom", "annual_qty"]}}}, "required": ["lines"]}},
    {"name": "update_lines", "description": "Change fields on existing lines, by line id.",
     "schema": {"type": "object", "properties": {"updates": {"type": "array", "items": {"type": "object", "properties": {"id": {"type": "string"}, **LINE_PROPS},
                                                                                    "required": ["id"]}}}, "required": ["updates"]}},
    {"name": "scale_quantities", "description": "Multiply annual quantities by a percentage change for some or all lines (arithmetic is done exactly).",
     "schema": {"type": "object", "properties": {"pct_change": {"type": "number"}, "line_ids": {"type": "array", "items": {"type": "string"}}}, "required": ["pct_change"]}},
    {"name": "remove_lines", "description": "Remove lines by id.",
     "schema": {"type": "object", "properties": {"ids": {"type": "array", "items": {"type": "string"}}}, "required": ["ids"]}},
    {"name": "set_questionnaire", "description": "Replace the vendor questionnaire. Each mandatory question needs a pass rule so answers can be scored "
     "without argument later: rule_kind yes | yes_and_valid (yes plus a certificate valid on the award date) | max | min (with rule_value) | none.",
     "schema": {"type": "object", "properties": {"questions": {"type": "array", "items": {"type": "object", "properties": {
         "text": {"type": "string"}, "type": {"type": "string", "enum": ["yes_no", "number", "text"]}, "mandatory": {"type": "boolean"},
         "rule_kind": {"type": "string", "enum": ["yes", "yes_and_valid", "max", "min", "none"]}, "rule_value": {"type": "number"}},
         "required": ["text", "type", "mandatory", "rule_kind"]}}}, "required": ["questions"]}},
    {"name": "set_terms", "description": "Replace the commercial terms (list of short clauses).",
     "schema": {"type": "object", "properties": {"terms": {"type": "array", "items": {"type": "string"}}}, "required": ["terms"]}},
    {"name": "set_vendors", "description": "Choose which vendors from the vendor master receive the RFx.",
     "schema": {"type": "object", "properties": {"vendor_ids": {"type": "array", "items": {"type": "string"}}}, "required": ["vendor_ids"]}},
]

SYSTEM = """You are an RFx co-pilot for a category buyer at an Indian retailer. The buyer describes what they need in conversation and you build the RFx draft with your tools: header and scope, line items, a vendor questionnaire, commercial terms, and the vendor list. The buyer sees the draft update beside this chat.

- Do what was asked, not more. If the buyer asks for line items, do not also invent a questionnaire or pick vendors; mention in one line what is still open instead.
- Act, then report. If the buyer's intent is clear, make the change with tools and say what you did in a sentence or two. Ask a question only when a wrong guess would be expensive, and ask at most two at a time.
- Do not retype line items the contract repository already has; import them.
- You are drafting for comparability. The quotes that come back will be compared line by line, so push for: one unit of measure per line, full dimensions and board spec, an explicit price basis in the terms (per RFx unit, delivered where, tax treatment, currency), and a pass rule on every mandatory questionnaire question. If the draft is missing one of these, say so once, briefly, and offer the fix.
- Never do quantity arithmetic yourself; use scale_quantities or import_last_year_lines.
- Do not describe the whole draft back to the buyer; they can see it. Mention only what changed and what is still open.
- Plain, brief language. No headings, no bullet lists unless listing open questions.

Today is 2026-11-01. Current draft summary is given with each message."""


def summary(rfx):
    cats = {}
    for l in rfx["lines"]:
        cats[l.get("category") or l.get("kind")] = cats.get(l.get("category") or l.get("kind"), 0) + 1
    return json.dumps({"title": rfx["title"], "scope_set": bool(rfx["scope"]), "due_date": rfx["due_date"], "award_date": rfx["award_date"],
                       "line_count": len(rfx["lines"]), "lines_by_category": cats,
                       "lines": [{"id": l["id"], "d": l["description"], "uom": l["uom"], "qty": l["annual_qty"]} for l in rfx["lines"]],
                       "questionnaire": [{"q": q["text"], "mandatory": q["mandatory"], "rule": q.get("rule")} for q in rfx["questionnaire"]],
                       "terms": rfx["terms"], "vendors": [v["id"] for v in rfx["vendors"]]}, ensure_ascii=False)


def _renumber(rfx):
    for i, l in enumerate(rfx["lines"], 1):
        l["id"] = f"L{i:02d}"


def _finish_line(l):
    l.setdefault("category", {"rsc": f"{l.get('ply') or 3}-ply RSC", "mailer": "Die-cut mailer", "partition": "Partition",
                              "pad": "Layer pad", "roll": "Corrugated roll"}.get(l.get("kind"), "Other"))
    l.setdefault("board_spec", ""); l.setdefault("print", "Plain")
    l["annual_qty"] = int(l.get("annual_qty") or 0)
    l["est_weight_g"] = est_weight_g(l.get("kind"), l.get("ply"), l.get("length_mm"), l.get("width_mm"), l.get("height_mm"), l.get("board_spec"))
    return l


READ_ONLY = {"get_last_year_contract", "get_vendor_master"}
LOCKED = """

THIS RFx HAS BEEN SENT to vendors{when}{replies}. It is locked: you cannot change the title, scope, lines, questionnaire, terms or vendors, and you have no tools to do so. You can still answer questions about it. If the buyer asks for a change, say plainly that the RFx has already gone out and vendors have quoted against it, so changing it now would make their replies incomparable; to change it they should Reset and start a new draft (and re-send). Do not pretend to make the change."""


def chat(message):
    rfx = db.get("rfx") or json.loads(json.dumps(EMPTY))
    seed = db.seed_rfx()
    changes = []
    locked = rfx.get("status") == "sent"          # enforced here, not only in the prompt: no write tool runs and nothing is saved

    def execute(name, a):
        if locked and name not in READ_ONLY:
            return {"error": "The RFx has been sent and is locked. Nothing was changed."}
        if name == "get_last_year_contract":
            return {"contract_ref": seed["last_year"]["contract_ref"], "incumbent": "V5 Om Sai Packaging",
                    "lines": [{"category": l["category"], "description": l["description"], "board_spec": l["board_spec"], "uom": l["uom"],
                               "annual_qty": l["annual_qty"], "price_inr": l["last_year_price"]} for l in seed["lines"]],
                    "annual_value_inr": round(sum(l["annual_qty"] * l["last_year_price"] for l in seed["lines"])),
                    "questionnaire_used": [q["text"] for q in seed["questionnaire"]], "terms_used": seed["terms"]}
        if name == "get_vendor_master":
            return {"vendors": seed["vendors"]}
        if name == "set_header":
            for k in ("title", "scope", "due_date", "award_date"):
                if a.get(k):
                    rfx[k] = a[k]
            if rfx["id"] == "RFQ-DRAFT":
                rfx["id"] = seed["id"]
            changes.append("header"); return {"ok": True}
        if name == "import_last_year_lines":
            f = 1 + (a.get("qty_change_pct") or 0) / 100
            cats = a.get("categories")
            n = 0
            for l in seed["lines"]:
                if cats and l["category"] not in cats:
                    continue
                nl = dict(l); nl["annual_qty"] = int(round(l["annual_qty"] * f, -2)) if f != 1 else l["annual_qty"]
                rfx["lines"].append(nl); n += 1
            rfx["last_year"] = seed["last_year"]
            _renumber(rfx); changes.append(f"{n} lines imported")
            return {"ok": True, "lines_imported": n, "total_lines": len(rfx["lines"])}
        if name == "add_lines":
            for l in a["lines"]:
                rfx["lines"].append(_finish_line(dict(l)))
            _renumber(rfx); changes.append(f"{len(a['lines'])} lines added")
            return {"ok": True, "ids": [l["id"] for l in rfx["lines"][-len(a["lines"]):]]}
        if name == "update_lines":
            done = []
            for u in a["updates"]:
                l = next((x for x in rfx["lines"] if x["id"] == u["id"]), None)
                if l:
                    l.update({k: v for k, v in u.items() if k != "id"}); _finish_line(l); done.append(u["id"])
            changes.append(f"{len(done)} lines updated"); return {"ok": True, "updated": done}
        if name == "scale_quantities":
            f = 1 + a["pct_change"] / 100
            ids = set(a.get("line_ids") or [l["id"] for l in rfx["lines"]])
            for l in rfx["lines"]:
                if l["id"] in ids:
                    l["annual_qty"] = int(round(l["annual_qty"] * f, -2))
            changes.append("quantities scaled"); return {"ok": True, "lines_changed": len(ids)}
        if name == "remove_lines":
            rfx["lines"] = [l for l in rfx["lines"] if l["id"] not in set(a["ids"])]
            _renumber(rfx); changes.append("lines removed"); return {"ok": True, "total_lines": len(rfx["lines"]), "note": "Line ids were renumbered."}
        if name == "set_questionnaire":
            qs = []
            for i, q in enumerate(a["questions"], 1):
                rk = q.get("rule_kind", "none")
                rule = None if rk == "none" else {"kind": rk, **({"value": q.get("rule_value")} if rk in ("max", "min") else {})}
                pt = {"yes": "Yes", "yes_and_valid": "Yes, and certificate valid on the award date"}.get(rk) or (
                    f"{q.get('rule_value'):g} or {'lower' if rk == 'max' else 'higher'}" if rk in ("max", "min") and q.get("rule_value") is not None else "Information only")
                qs.append({"id": f"Q{i}", "text": q["text"], "type": q["type"], "mandatory": bool(q["mandatory"]) and rule is not None,
                           "rule": rule, "pass_text": pt})
            rfx["questionnaire"] = qs; changes.append("questionnaire"); return {"ok": True, "questions": len(qs)}
        if name == "set_terms":
            rfx["terms"] = a["terms"]; changes.append("terms"); return {"ok": True}
        if name == "set_vendors":
            rfx["vendors"] = [v for v in seed["vendors"] if v["id"] in set(a["vendor_ids"])]
            changes.append("vendors"); return {"ok": True, "vendors": [v["name"] for v in rfx["vendors"]]}
        return {"error": f"Unknown tool {name}"}

    history = db.get("copilot_chat", [])
    mh = [{"role": h["role"], "text": h["text"]} for h in history[-14:]]
    mh.append({"role": "user", "text": f"{message}\n\n[Current draft: {summary(rfx)}]"})
    system, tools = SYSTEM, TOOLS
    if locked:
        n = len(db.rows("SELECT 1 FROM responses WHERE extraction IS NOT NULL"))
        system += LOCKED.format(when=f" on {rfx['sent_at']}" if rfx.get("sent_at") else "", replies=f" and {n} responses have been read" if n else "")
        tools = [t for t in TOOLS if t["name"] in READ_ONLY]
    llm.begin_turn()
    text = llm.run_tools(system, mh, tools, execute, max_steps=10)
    if locked:
        changes = []
    else:
        db.put("rfx", rfx)
    history += [{"role": "user", "text": message}, {"role": "assistant", "text": text, "changes": changes, "model": llm.model_name(), "locked": locked}]
    db.put("copilot_chat", history)
    return {"text": text, "changes": changes, "rfx": rfx}
