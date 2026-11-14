"""The analyst: natural-language questions over the levelled comparison.

The model never does arithmetic. It writes SQL against a read-only snapshot of the comparison, or calls the
award engine, and reports what came back. Every answer ships with the queries that produced it, and each
figure in the prose is traced back to a tool result.
"""
import itertools
import json
import re
import sqlite3
import time

from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill

from . import db, llm, normalize

STATUS_RANK = {"verified": 0, "assumed": 1, "needs_review": 2}


def snapshot(comp):
    """In-memory SQLite copy of the comparison. Rebuilt per question, so it always reflects buyer decisions."""
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript("""
    CREATE TABLE lines (line_id TEXT, category TEXT, description TEXT, board_spec TEXT, uom TEXT, annual_qty REAL,
                        est_weight_g REAL, last_year_price_inr REAL, last_year_value_inr REAL);
    CREATE TABLE vendors (vendor_id TEXT, vendor_name TEXT, incumbent INT, responded INT, lines_quoted INT, quality_status TEXT,
                          freight TEXT, payment_terms TEXT, lead_time_days REAL, validity TEXT, quoted_currency TEXT,
                          discount_pct REAL, discount_condition TEXT, discount_applied INT, open_issues TEXT);
    CREATE TABLE quotes (vendor_id TEXT, vendor_name TEXT, line_id TEXT, unit_price_inr REAL, annual_value_inr REAL,
                         status TEXT, flags TEXT, as_quoted TEXT, spec_deviation TEXT);
    CREATE TABLE not_quoted (vendor_id TEXT, vendor_name TEXT, line_id TEXT, reason TEXT);
    CREATE TABLE questionnaire (vendor_id TEXT, vendor_name TEXT, question_id TEXT, question TEXT, mandatory INT,
                                answer TEXT, result TEXT, note TEXT);
    CREATE TABLE documents (vendor_id TEXT, vendor_name TEXT, filename TEXT, doc_type TEXT, summary TEXT, valid_until TEXT);
    """)
    for l in comp["rfx"]["lines"]:
        ly = l.get("last_year_price")
        c.execute("INSERT INTO lines VALUES (?,?,?,?,?,?,?,?,?)", (l["id"], l.get("category"), l["description"], l.get("board_spec"), l["uom"],
                  l["annual_qty"], l.get("est_weight_g"), ly, ly * l["annual_qty"] if ly else None))
    names = {}
    for v in comp["vendors"]:
        names[v["id"]] = v["name"]
        t = v["terms"] or {}
        d = (t.get("discounts") or [{}])[0]
        cur = sorted({x["raw"]["currency"] for x in comp["cells"] if x["vendor_id"] == v["id"] and x["raw"] and x["raw"].get("currency")})
        c.execute("INSERT INTO vendors VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (
            v["id"], v["name"], int(v["incumbent"]), int(v["response_status"] == "extracted"), v.get("lines_quoted", 0), v["quality"],
            (t.get("freight") or {}).get("status"), t.get("payment_terms"), t.get("lead_time_days"), t.get("validity"), "/".join(cur) or None,
            d.get("pct"), d.get("condition"), int(bool(d) and v["settings"]["apply_discount"]), " | ".join(f["text"] for f in v["flags"]) or None))
        for q in v["questionnaire"]:
            c.execute("INSERT INTO questionnaire VALUES (?,?,?,?,?,?,?,?)", (v["id"], v["name"], q["question_id"], q["question"],
                      int(q["mandatory"]), q["answer"], q["result"], q["note"]))
        for dd in v["documents"]:
            c.execute("INSERT INTO documents VALUES (?,?,?,?,?,?)", (v["id"], v["name"], dd.get("filename"), dd.get("doc_type"),
                      dd.get("summary"), dd.get("valid_until")))
    for x in comp["cells"]:
        if x["price"] is not None:
            raw = x["raw"] or {}
            asq = f"{raw.get('currency')} {raw.get('amount')} per {raw.get('per_qty'):g} {raw.get('uom')}" if raw.get("amount") is not None else raw.get("reference")
            dev = next((f["text"] for f in x["flags"] if f["code"] == "SPEC"), None)
            c.execute("INSERT INTO quotes VALUES (?,?,?,?,?,?,?,?,?)", (x["vendor_id"], names[x["vendor_id"]], x["line_id"], round(x["price"], 4),
                      round(x["annual_value"], 2), x["status"], "; ".join(f["code"] for f in x["flags"]) or None, asq, dev))
        elif x["status"] in ("not_quoted", "needs_review"):
            c.execute("INSERT INTO not_quoted VALUES (?,?,?,?)", (x["vendor_id"], names[x["vendor_id"]], x["line_id"],
                      x.get("reason") or "Price present but unusable: " + "; ".join(f["text"] for f in x["flags"])))
    c.commit()
    c.execute("PRAGMA query_only = ON")
    return c


def award_scenario(comp, eligible_vendor_ids=None, max_vendors=None, allowed_statuses=None):
    """Cheapest allocation line by line under constraints. Exhaustive over vendor subsets (5 vendors = 31 subsets)."""
    lines = comp["rfx"]["lines"]
    names = {v["id"]: v["name"] for v in comp["vendors"]}
    elig = [v for v in (eligible_vendor_ids or list(names)) if v in names]
    allowed = set(allowed_statuses or ["verified", "assumed"])       # a price that needs review cannot win a line by default
    price = {(x["vendor_id"], x["line_id"]): x for x in comp["cells"] if x["price"] is not None and x["status"] in allowed}
    held_back = [{"vendor_name": names[x["vendor_id"]], "line_id": x["line_id"], "unit_price_inr": round(x["price"], 4),
                  "why": "; ".join(f["code"] for f in x["flags"] if f["severity"] == "needs_review")}
                 for x in comp["cells"] if x["price"] is not None and x["status"] not in allowed and x["vendor_id"] in elig]

    def allocate(subset):
        alloc, total, uncovered = [], 0.0, []
        for l in lines:
            opts = [(price[(v, l["id"])]["price"], v) for v in subset if (v, l["id"]) in price]
            if not opts:
                uncovered.append(l["id"]); continue
            p, v = min(opts)
            runner = sorted(o for o in opts if o[1] != v)
            alloc.append({"line_id": l["id"], "description": l["description"], "vendor_id": v, "vendor_name": names[v],
                          "unit_price_inr": round(p, 4), "annual_qty": l["annual_qty"], "annual_value_inr": round(p * l["annual_qty"], 2),
                          "status": price[(v, l["id"])]["status"],
                          "next_best_vendor": names[runner[0][1]] if runner else None,
                          "next_best_price_inr": round(runner[0][0], 4) if runner else None})
            total += p * l["annual_qty"]
        return alloc, total, uncovered

    k = max_vendors or len(elig)
    best = None
    for n in range(1, min(k, len(elig)) + 1):
        for subset in itertools.combinations(elig, n):
            alloc, total, unc = allocate(subset)
            key = (len(unc), total)                         # cover as many lines as possible first, then minimise cost
            if best is None or key < best[0]:
                best = (key, alloc, total, unc, subset)
    if best is None:
        return {"error": "No eligible vendors."}
    _, alloc, total, unc, subset = best
    # What each constraint costs, computed here so the model never has to subtract.
    everyone = [v for v in names]
    _, open_total, open_unc = allocate(everyone)
    add_one = []
    if not max_vendors:
        for v in everyone:
            if v not in elig:
                _, t2, u2 = allocate(elig + [v])
                add_one.append({"vendor_name": names[v], "total_if_added_inr": round(t2, 2), "saving_if_added_inr": round(total - t2, 2),
                                "lines_uncovered_if_added": len(u2)})
    covered = {a["line_id"] for a in alloc}
    ly = sum((l.get("last_year_price") or 0) * l["annual_qty"] for l in lines if l["id"] in covered)
    by_vendor = {}
    for a in alloc:
        d = by_vendor.setdefault(a["vendor_id"], {"vendor_id": a["vendor_id"], "vendor_name": a["vendor_name"], "lines": 0, "annual_value_inr": 0.0})
        d["lines"] += 1; d["annual_value_inr"] += a["annual_value_inr"]
    for d in by_vendor.values():
        d["annual_value_inr"] = round(d["annual_value_inr"], 2); d["share_pct"] = round(100 * d["annual_value_inr"] / total, 1) if total else 0
    at_risk = {s: round(sum(a["annual_value_inr"] for a in alloc if a["status"] == s), 2) for s in ("assumed", "needs_review")}
    return {"constraints": {"eligible_vendors": [names[v] for v in elig], "max_vendors": max_vendors, "allowed_statuses": sorted(allowed)},
            "total_annual_value_inr": round(total, 2), "lines_covered": len(alloc), "lines_uncovered": unc,
            "last_year_value_same_lines_inr": round(ly, 2), "saving_vs_last_year_inr": round(ly - total, 2),
            "saving_vs_last_year_pct": round(100 * (ly - total) / ly, 2) if ly else None,
            "unconstrained_split_total_inr": round(open_total, 2), "unconstrained_split_lines_uncovered": len(open_unc),
            "cost_of_constraints_inr": round(total - open_total, 2),
            "if_one_excluded_vendor_were_added": sorted(add_one, key=lambda d: -d["saving_if_added_inr"]),
            "by_vendor": sorted(by_vendor.values(), key=lambda d: -d["annual_value_inr"]),
            "value_on_assumed_prices_inr": at_risk["assumed"], "value_on_needs_review_prices_inr": at_risk["needs_review"],
            "prices_held_back_pending_review": held_back,
            "lines_on_unverified_prices": sum(1 for a in alloc if a["status"] != "verified"),
            "allocation": alloc}


TOOLS = [
    {"name": "query", "description": "Run one read-only SQLite SELECT over the comparison and get the rows back. Use this for every number you "
     "report. Set show=true to also display the result to the buyer as a table.",
     "schema": {"type": "object", "properties": {"sql": {"type": "string"}, "title": {"type": "string", "description": "Short table title for the buyer"},
                                                  "show": {"type": "boolean"}}, "required": ["sql", "title"]}},
    {"name": "award_scenario", "description": "Compute the cheapest line-by-line award under constraints, exactly (exhaustive search). Returns totals, "
     "split by vendor, uncovered lines, saving vs last year, how much value rests on assumed prices, which prices were held back because they need review, what the constraints cost versus an unconstrained split, and what adding each excluded vendor back would save. The allocation is shown to the buyer as a table.",
     "schema": {"type": "object", "properties": {
         "title": {"type": "string"},
         "eligible_vendor_ids": {"type": "array", "items": {"type": "string"}, "description": "Vendor ids allowed to win lines. Omit for all."},
         "max_vendors": {"type": "integer", "description": "Maximum number of vendors in the award. 1 = single-source."},
         "allowed_statuses": {"type": "array", "items": {"type": "string", "enum": ["verified", "assumed"]},
                              "description": "Which price statuses may win a line. Default is both. needs_review prices can never win: they are held back and listed until the buyer resolves them on the Compare screen."}}, "required": ["title"]}},
    {"name": "chart", "description": "Show the buyer a chart built from a SQL result. First column of the result is the category axis; "
     "each further numeric column is a series.",
     "schema": {"type": "object", "properties": {"sql": {"type": "string"}, "title": {"type": "string"},
                                                  "kind": {"type": "string", "enum": ["bar", "stacked_bar", "line"]},
                                                  "y_label": {"type": "string"}}, "required": ["sql", "title", "kind"]}},
    {"name": "export_xlsx", "description": "Create an Excel file for download. Each sheet is a SQL result, or the allocation of the most recent award_scenario "
     "(use source='last_scenario'). A sheet listing every assumption and open flag is always appended.",
     "schema": {"type": "object", "properties": {"filename": {"type": "string"}, "sheets": {"type": "array", "items": {"type": "object", "properties": {
         "name": {"type": "string"}, "source": {"type": "string", "enum": ["sql", "last_scenario"]}, "sql": {"type": "string"}}, "required": ["name", "source"]}}},
         "required": ["filename", "sheets"]}},
]

SYSTEM = """You are the analyst for a procurement buyer who is about to award a contract. The buyer asks questions in plain language about vendor quotes that have already been extracted and normalised to one unit and one currency (INR, per RFx unit, annual quantities).

How you work:
- Every figure you state must come from a tool result in this turn. Never calculate in your head, never estimate, never round a number into a different number. If you need a sum, a difference or a percentage, get it from SQL or from award_scenario.
- Use award_scenario for any "who should we award to / what if we split / cheapest under constraint" question. Use query for everything else.
- Prefer one or two well-built queries over many small ones. Show a table or chart when it helps the buyer decide; do not show tables that just repeat your sentence.

Honesty about the data, which is the point of this product:
- Each price has a status. verified = read cleanly with nothing assumed, or confirmed by the buyer. assumed = usable but depends on a stated assumption (currency rate, a calculated box weight, last year's price, a photo reading). needs_review = something is probably wrong or not like-for-like.
- When an answer depends on assumed or needs_review prices, say so in one plain sentence with the amount of money involved, and say what would change the answer. Do not bury it and do not pad it.
- quality_status: 'cleared' means every mandatory questionnaire answer passed. 'failed' means at least one mandatory answer failed. 'incomplete' means the vendor did not answer; that is NOT cleared. Never treat incomplete as cleared, and say who is excluded for that reason, because chasing an answer may be cheaper than excluding a vendor.
- Missing quotes are missing, not zero. Vendors who quoted fewer lines have smaller totals; never compare raw totals across vendors with different coverage without saying so. Compare on common lines instead.
- vendors.open_issues lists vendor-level comparability problems (for example freight not included). Check it before recommending a vendor.
- If the question cannot be answered from the data, say what is missing.

- To say what a constraint (quality gate, vendor limit, exclusion) costs, run the constrained scenario and quote its cost_of_constraints_inr and if_one_excluded_vendor_were_added. Never infer the cost by comparing two totals yourself.
- Prices with status needs_review are always held back from award scenarios until the buyer resolves them. If held-back prices would have won lines, say how many and for which vendor, in one sentence.
- Write money the way an Indian buyer reads it: ₹3.70 crore, ₹2.13 lakh, ₹4.00 per piece. Never print full rupee-and-paise amounts like ₹3,70,39,800.85.
- Counts matter too: if you say "N lines" or "N vendors", N must come from a query result (use COUNT), never from your own counting.
- Each turn stands alone: re-query any number you repeat from earlier in the conversation, because the buyer may have changed the data since.

Style: lead with the answer in one or two sentences. Then only what changes the decision. Aim for under 130 words unless the buyer asks for detail; the tables and scenario cards carry the detail. Do not walk through individual flags or unit conversions unless asked. Refer to vendors by short name, not id. Indian number formatting: lakh and crore for large rupee amounts are fine if you also got the exact figure from a tool. No preamble, no restating the question, no generic procurement advice.

Database (SQLite, read-only):
lines(line_id, category, description, board_spec, uom, annual_qty, est_weight_g, last_year_price_inr, last_year_value_inr)
vendors(vendor_id, vendor_name, incumbent, responded, lines_quoted, quality_status, freight, payment_terms, lead_time_days, validity, quoted_currency, discount_pct, discount_condition, discount_applied, open_issues)
quotes(vendor_id, vendor_name, line_id, unit_price_inr, annual_value_inr, status, flags, as_quoted, spec_deviation)   -- one row per usable price
not_quoted(vendor_id, vendor_name, line_id, reason)   -- lines a vendor did not price, or priced unusably
questionnaire(vendor_id, vendor_name, question_id, question, mandatory, answer, result, note)   -- result: pass | fail | unknown | info
documents(vendor_id, vendor_name, filename, doc_type, summary, valid_until)
"""


def _rows(cur, limit=400):
    cols = [d[0] for d in cur.description]
    return cols, [list(r) for r in cur.fetchmany(limit)]


def _numbers(obj, acc):
    if isinstance(obj, bool):
        return
    if isinstance(obj, (int, float)):
        acc.add(abs(float(obj)))
    elif isinstance(obj, dict):
        for v in obj.values():
            _numbers(v, acc)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            _numbers(v, acc)


MONEY = re.compile(r"(?:₹|Rs\.?|INR)\s*([\d,]+(?:\.\d+)?)\s*(crore|cr|lakh|lac|L)?\b|([\d,]+(?:\.\d+)?)\s*(crore|cr|lakh|lac|%)", re.I)


def trace_figures(text, seen):
    """Every rupee amount and percentage in the prose should equal a number a tool returned (allowing display rounding)."""
    figures = []
    for m in MONEY.finditer(text):
        num, unit = (m.group(1), m.group(2)) if m.group(1) else (m.group(3), m.group(4))
        try:
            val = float(num.replace(",", ""))
        except ValueError:
            continue
        u = (unit or "").lower()
        scale = 1e7 if u in ("crore", "cr") else 1e5 if u in ("lakh", "lac", "l") else 1
        target = val * scale
        decimals = len(num.split(".")[1]) if "." in num else 0
        tol = max(0.5 * scale * 10 ** (-decimals) * 1.02, 0.005)       # half a unit in the last displayed digit
        ok = any(abs(s - target) <= tol for s in seen)
        figures.append({"text": m.group(0).strip(), "traced": ok})
    for m in re.finditer(r"\b(\d{1,3})\s+(?:of\s+(?:the\s+)?\d+\s+)?(lines?|vendors?|line items?|suppliers?)\b", text, re.I):
        figures.append({"text": m.group(0).strip(), "traced": any(abs(s - float(m.group(1))) < 1e-9 for s in seen)})
    return figures


def ask(question):
    comp = normalize.build()
    if not comp:
        return {"text": "There is no RFx yet.", "blocks": [], "trace": [], "figures": []}
    snap = snapshot(comp)
    blocks, trace, seen = [], [], {float(len(comp["rfx"]["lines"])), float(len(comp["vendors"]))}
    state = {"scenario": None}

    def execute(name, a):
        if name == "query":
            sql = a["sql"].strip().rstrip(";")
            if not re.match(r"(?is)^\s*(select|with)\b", sql):
                return {"error": "Only SELECT statements are allowed."}
            cols, rows = _rows(snap.execute(sql))
            _numbers(rows, seen); seen.add(float(len(rows)))
            trace.append({"tool": "query", "title": a.get("title"), "sql": sql, "rows": len(rows)})
            if a.get("show"):
                blocks.append({"type": "table", "title": a.get("title"), "columns": cols, "rows": rows[:200], "sql": sql})
            return {"columns": cols, "rows": rows, "row_count": len(rows)}
        if name == "award_scenario":
            statuses = [x for x in (a.get("allowed_statuses") or []) if x != "needs_review"] or None   # enforced, not just prompted
            res = award_scenario(comp, a.get("eligible_vendor_ids"), a.get("max_vendors"), statuses)
            if "error" in res:
                return res
            state["scenario"] = res
            _numbers(res, seen); seen.update({float(len(res["by_vendor"])), float(len(res["lines_uncovered"])), float(len(res["prices_held_back_pending_review"]))})
            trace.append({"tool": "award_scenario", "title": a.get("title"), "args": {k: v for k, v in a.items() if k != "title"}})
            blocks.append({"type": "scenario", "title": a.get("title"), "scenario": res})
            return res
        if name == "chart":
            sql = a["sql"].strip().rstrip(";")
            if not re.match(r"(?is)^\s*(select|with)\b", sql):
                return {"error": "Only SELECT statements are allowed."}
            cols, rows = _rows(snap.execute(sql), 60)
            if len(cols) < 2 or not rows:
                return {"error": "A chart needs a label column, at least one numeric column, and at least one row."}
            _numbers(rows, seen)
            trace.append({"tool": "chart", "title": a.get("title"), "sql": sql, "rows": len(rows)})
            blocks.append({"type": "chart", "title": a.get("title"), "kind": a.get("kind", "bar"), "y_label": a.get("y_label"),
                           "labels": [str(r[0]) for r in rows],
                           "series": [{"name": cols[i], "data": [r[i] for r in rows]} for i in range(1, len(cols))], "sql": sql})
            return {"ok": True, "points": len(rows), "columns": cols, "rows": rows}
        if name == "export_xlsx":
            wb = Workbook(); wb.remove(wb.active)
            bold, fill = Font(bold=True, color="FFFFFF"), PatternFill("solid", fgColor="1F3A5F")
            def sheet(title, cols, rows):
                ws = wb.create_sheet(re.sub(r"[\[\]\*\?/\\:]", " ", title)[:31])
                ws.append(cols)
                for cell in ws[1]:
                    cell.font, cell.fill = bold, fill
                for r in rows:
                    ws.append(r)
                for i, col in enumerate(cols):
                    ws.column_dimensions[ws.cell(row=1, column=i + 1).column_letter].width = min(48, max(12, len(str(col)) + 4))
            for s in a["sheets"]:
                if s["source"] == "last_scenario":
                    if not state["scenario"]:
                        return {"error": "No award_scenario has been run in this turn. Run it first."}
                    al = state["scenario"]["allocation"]
                    sheet(s["name"], list(al[0].keys()), [list(r.values()) for r in al])
                    sc = state["scenario"]
                    sheet("Scenario summary", ["Item", "Value"], [
                        ["Constraints", json.dumps(sc["constraints"])], ["Total annual value (INR)", sc["total_annual_value_inr"]],
                        ["Lines covered", sc["lines_covered"]], ["Lines uncovered", ", ".join(sc["lines_uncovered"]) or "none"],
                        ["Last year, same lines (INR)", sc["last_year_value_same_lines_inr"]], ["Saving vs last year (INR)", sc["saving_vs_last_year_inr"]],
                        ["Value on assumed prices (INR)", sc["value_on_assumed_prices_inr"]],
                        ["Value on needs-review prices (INR)", sc["value_on_needs_review_prices_inr"]]])
                else:
                    cols, rows = _rows(snap.execute(s["sql"].strip().rstrip(";")), 5000)
                    sheet(s["name"], cols, rows)
            flags = [[x["vendor_id"], x["line_id"], x["status"], f["code"], f["text"]] for x in comp["cells"] for f in x["flags"]]
            flags += [[v["id"], "(all lines)", f["severity"], f["code"], f["text"]] for v in comp["vendors"] for f in v["flags"]]
            sheet("Assumptions and flags", ["Vendor", "Line", "Status", "Code", "What to know"], flags)
            name_ = re.sub(r"[^A-Za-z0-9_\-]", "_", a["filename"].rsplit(".", 1)[0])[:60] + f"_{int(time.time())}.xlsx"
            db.EXPORTS.mkdir(parents=True, exist_ok=True)
            wb.save(db.EXPORTS / name_)
            trace.append({"tool": "export_xlsx", "title": name_})
            blocks.append({"type": "file", "name": name_, "url": f"/exports/{name_}"})
            return {"ok": True, "file": name_}
        return {"error": f"Unknown tool {name}"}

    history = db.get("analyst_chat", [])
    model_history = [{"role": h["role"], "text": h["text"] + (h.get("trace_text") or "")} for h in history[-12:]]
    model_history.append({"role": "user", "text": question})
    rfx, st = comp["rfx"], comp["settings"]
    context = (f"\nFacts for this RFx: {rfx['id']}, {len(rfx['lines'])} lines, {len(comp['vendors'])} vendors invited, award date {rfx.get('award_date')}. "
               f"Foreign-currency quotes were converted at {json.dumps(st['fx'])} ({st.get('fx_note')}). The buyer can change this rate on the Compare screen.")
    text = llm.run_tools(SYSTEM + context, model_history, TOOLS, execute, max_steps=14)
    figures = trace_figures(text, seen)
    trace_text = ""
    if trace:
        trace_text = "\n\n[Tools used in this turn: " + "; ".join(
            f"{t['tool']}({t.get('sql') or json.dumps(t.get('args', {}))})" for t in trace) + "]"
    turn = {"role": "assistant", "text": text, "blocks": blocks, "trace": trace, "figures": figures, "trace_text": trace_text,
            "model": llm.model_name()}
    history += [{"role": "user", "text": question}, turn]
    db.put("analyst_chat", history)
    db.audit("analyst", question[:200])
    return turn
