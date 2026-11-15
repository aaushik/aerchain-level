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
    """In-memory SQLite copy of the comparison. Rebuilt per question, so it always reflects buyer decisions.
    Vendors are keyed by their short name, never an opaque id: a model that has to write 'V2' will sooner or later
    write it for the wrong vendor, and a name in a WHERE clause is checkable by anyone reading the query."""
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript("""
    CREATE TABLE lines (line_id TEXT, category TEXT, description TEXT, board_spec TEXT, uom TEXT, annual_qty REAL,
                        est_weight_g REAL, last_year_price_inr REAL, last_year_value_inr REAL);
    CREATE TABLE vendors (vendor TEXT, vendor_name TEXT, incumbent INT, responded INT, lines_quoted INT, quality_status TEXT,
                          freight TEXT, freight_pct REAL, held_back_from_award TEXT, payment_terms TEXT, lead_time_days REAL, validity TEXT,
                          quoted_currency TEXT, discount_pct REAL, discount_condition TEXT, discount_applied INT,
                          vendor_wide_assumptions TEXT, open_issues TEXT);
    CREATE TABLE quotes (vendor TEXT, vendor_name TEXT, line_id TEXT, unit_price_inr REAL, annual_value_inr REAL,
                         status TEXT, cell_status TEXT, award_eligible INT, flags TEXT, as_quoted TEXT, spec_deviation TEXT);
    CREATE TABLE not_quoted (vendor TEXT, vendor_name TEXT, line_id TEXT, reason TEXT);
    CREATE TABLE questionnaire (vendor TEXT, vendor_name TEXT, question_id TEXT, question TEXT, mandatory INT,
                                answer TEXT, result TEXT, note TEXT);
    CREATE TABLE documents (vendor TEXT, vendor_name TEXT, filename TEXT, doc_type TEXT, summary TEXT, valid_until TEXT);
    """)
    for l in comp["rfx"]["lines"]:
        ly = l.get("last_year_price")
        c.execute("INSERT INTO lines VALUES (?,?,?,?,?,?,?,?,?)", (l["id"], l.get("category"), l["description"], l.get("board_spec"), l["uom"],
                  l["annual_qty"], l.get("est_weight_g"), ly, ly * l["annual_qty"] if ly else None))
    names, held = {}, set()
    for v in comp["vendors"]:
        names[v["id"]] = (v["short"], v["name"])
        if v.get("freight_held"):
            held.add(v["id"])
        t = v["terms"] or {}
        d = (t.get("discounts") or [{}])[0]
        cur = sorted({x["raw"]["currency"] for x in comp["cells"] if x["vendor_id"] == v["id"] and x["raw"] and x["raw"].get("currency")})
        c.execute("INSERT INTO vendors VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (
            v["short"], v["name"], int(v["incumbent"]), int(v["response_status"] == "extracted"), v.get("lines_quoted", 0), v["quality"],
            (t.get("freight") or {}).get("status"), (v.get("freight_pct") or [None])[0],
            "Freight extra with no amount: prices are ex-works" if v.get("freight_held") else None,
            t.get("payment_terms"), t.get("lead_time_days"), t.get("validity"), "/".join(cur) or None,
            d.get("pct"), d.get("condition"), int(bool(d) and v["settings"]["apply_discount"]),
            " | ".join(w["text"] for w in v.get("wide_assumptions", [])) or None, " | ".join(f["text"] for f in v["flags"]) or None))
        for q in v["questionnaire"]:
            c.execute("INSERT INTO questionnaire VALUES (?,?,?,?,?,?,?,?)", (v["short"], v["name"], q["question_id"], q["question"],
                      int(q["mandatory"]), q["answer"], q["result"], q["note"]))
        for dd in v["documents"]:
            c.execute("INSERT INTO documents VALUES (?,?,?,?,?,?)", (v["short"], v["name"], dd.get("filename"), dd.get("doc_type"),
                      dd.get("summary"), dd.get("valid_until")))
    for x in comp["cells"]:
        if x["price"] is not None:
            raw = x["raw"] or {}
            asq = f"{raw.get('currency')} {raw.get('amount')} per {raw.get('per_qty'):g} {raw.get('uom')}" if raw.get("amount") is not None else raw.get("reference")
            dev = next((f["text"] for f in x["flags"] if f["code"] == "SPEC"), None)
            c.execute("INSERT INTO quotes VALUES (?,?,?,?,?,?,?,?,?,?,?)", (*names[x["vendor_id"]], x["line_id"], round(x["price"], 4),
                      round(x["annual_value"], 2), x["status"], x.get("cell_status", x["status"]),
                      int(x["status"] != "needs_review" and x["vendor_id"] not in held),
                      "; ".join(f["code"] for f in x["flags"]) or None, asq, dev))
        elif x["status"] in ("not_quoted", "needs_review"):
            c.execute("INSERT INTO not_quoted VALUES (?,?,?,?)", (*names[x["vendor_id"]], x["line_id"],
                      x.get("reason") or "Price present but unusable: " + "; ".join(f["text"] for f in x["flags"])))
    c.commit()
    c.execute("PRAGMA query_only = ON")
    return c


def resolve_vendors(comp, refs):
    """Vendor references as the model or buyer gives them (short name, full name, id, or a unique part of a name) -> ids."""
    out, bad = [], []
    for r in refs or []:
        k = str(r).strip().lower()
        hit = [v["id"] for v in comp["vendors"] if k in (v["id"].lower(), v["name"].lower(), v["short"].lower())]
        hit = hit or [v["id"] for v in comp["vendors"] if len(k) >= 3 and k in v["name"].lower()]
        if len(hit) == 1:
            out += [] if hit[0] in out else hit
        else:
            bad.append(str(r))
    return out, bad


def award_scenario(comp, eligible_vendors=None, max_vendors=None, allowed_statuses=None):
    """Cheapest allocation line by line under constraints. Exhaustive over vendor subsets (5 vendors = 31 subsets).
    Held back from winning: prices that need review, and every price from a vendor whose freight is extra with no figure
    (ex-works against delivered is not like-for-like). Both are listed with the reason, and compete once resolved."""
    lines = comp["rfx"]["lines"]
    names = {v["id"]: v["short"] for v in comp["vendors"]}
    if eligible_vendors:
        elig, bad = resolve_vendors(comp, eligible_vendors)
        if bad:
            return {"error": f"Unknown vendor(s) {bad}. Use the names in vendors.vendor: {sorted(names.values())}."}
    else:
        elig = list(names)
    held_vendors = {v["id"] for v in comp["vendors"] if v.get("freight_held")}
    allowed = set(allowed_statuses or ["verified", "assumed"])       # a price that needs review cannot win a line by default
    price = {(x["vendor_id"], x["line_id"]): x for x in comp["cells"]
             if x["price"] is not None and x["status"] in allowed and x["vendor_id"] not in held_vendors}
    held_back = []
    for x in comp["cells"]:
        if x["price"] is not None and x["vendor_id"] in elig and (x["vendor_id"], x["line_id"]) not in price:
            why = [f["code"] for f in x["flags"] if f["severity"] == "needs_review"] if x["status"] not in allowed else []
            if x["status"] not in allowed and not why:
                why = [f"status {x['status']} not allowed"]
            if x["vendor_id"] in held_vendors:
                why.append("FREIGHT_EXTRA")           # ex-works against delivered: lifts when a freight % is set
            held_back.append({"vendor_name": names[x["vendor_id"]], "line_id": x["line_id"], "unit_price_inr": round(x["price"], 4), "why": "; ".join(why)})

    def allocate(subset):
        alloc, total, uncovered = [], 0.0, []
        for l in lines:
            opts = [(price[(v, l["id"])]["price"], v) for v in subset if (v, l["id"]) in price]
            if not opts:
                uncovered.append(l["id"]); continue
            p, v = min(opts)
            runner = sorted(o for o in opts if o[1] != v)
            alloc.append({"line_id": l["id"], "description": l["description"], "vendor_name": names[v],
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
                if v in held_vendors or not any(k[0] == v for k in price):       # zero would read as "not worth chasing"; it is unknown
                    add_one.append({"vendor_name": names[v], "total_if_added_inr": None, "saving_if_added_inr": None, "lines_uncovered_if_added": None,
                                    "not_computable": "freight extra with no amount stated; set a freight % or get it from the vendor"
                                    if v in held_vendors else "none of its prices can win a line yet (all need review or are not allowed)"})
                    continue
                _, t2, u2 = allocate(elig + [v])
                add_one.append({"vendor_name": names[v], "total_if_added_inr": round(t2, 2), "saving_if_added_inr": round(total - t2, 2),
                                "lines_uncovered_if_added": len(u2)})
    covered = {a["line_id"] for a in alloc}
    ly = sum((l.get("last_year_price") or 0) * l["annual_qty"] for l in lines if l["id"] in covered)
    by_vendor = {}
    for a in alloc:
        d = by_vendor.setdefault(a["vendor_name"], {"vendor_name": a["vendor_name"], "lines": 0, "annual_value_inr": 0.0})
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
            "if_one_excluded_vendor_were_added": sorted(add_one, key=lambda d: (d["saving_if_added_inr"] is None, -(d["saving_if_added_inr"] or 0))),
            "by_vendor": sorted(by_vendor.values(), key=lambda d: -d["annual_value_inr"]),
            "value_on_assumed_prices_inr": at_risk["assumed"], "value_on_needs_review_prices_inr": at_risk["needs_review"],
            "prices_held_back_pending_review": held_back,
            "vendors_held_back_for_freight": [names[v] for v in elig if v in held_vendors],
            "lines_on_unverified_prices": sum(1 for a in alloc if a["status"] != "verified"),
            "allocation": alloc}


TOOLS = [
    {"name": "query", "description": "Run one read-only SQLite SELECT over the comparison and get the rows back. Use this for every number you "
     "report. Set show=true to also display the result to the buyer as a table.",
     "schema": {"type": "object", "properties": {"sql": {"type": "string"}, "title": {"type": "string", "description": "Short table title for the buyer"},
                                                  "show": {"type": "boolean"}}, "required": ["sql", "title"]}},
    {"name": "award_scenario", "description": "Compute the cheapest line-by-line award under constraints, exactly (exhaustive search). Returns totals, "
     "split by vendor, uncovered lines, saving vs last year, how much value rests on assumed prices, which prices were held back (needs review, or the vendor's freight is extra with no amount), what the constraints cost versus an unconstrained split, and what adding each excluded vendor back would save. The allocation is shown to the buyer as a table.",
     "schema": {"type": "object", "properties": {
         "title": {"type": "string"},
         "eligible_vendors": {"type": "array", "items": {"type": "string"}, "description": "Vendors allowed to win lines, by short name exactly as in vendors.vendor (for example 'Om Sai'). Omit for all."},
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

- To say what a constraint (quality gate, vendor limit, exclusion) costs, run the constrained scenario and quote its cost_of_constraints_inr and if_one_excluded_vendor_were_added. Never infer the cost by comparing two totals yourself. An entry with not_computable has no saving figure: say the saving is unknown and why (for example freight not stated); never call that vendor not worth chasing.
- Prices with status needs_review are always held back from award scenarios until the buyer resolves them. So are all prices from a vendor whose freight is extra with no amount stated (vendors.held_back_from_award): those prices are ex-works and the others are delivered, so they cannot win a line until the buyer sets a freight % on the Compare screen or the vendor states freight. quotes.award_eligible = 1 marks prices that can win. If held-back prices would have won lines, say how many and for which vendor, and why, in one sentence.
- Vendors: refer to them, and filter on them, only by the short name in the vendor column (WHERE vendor = 'Om Sai'). The roster for this RFx is given below. Never label a query, column or comment with a vendor other than the one it filters on; check the roster before naming a vendor.
- status counts every assumption. cell_status ignores assumptions that apply to every price from that vendor (vendors.vendor_wide_assumptions, for example a currency rate or a photo); use it to find which individual prices need a look.
- When you show a scenario card or a table, the buyer can read it: your prose gives the conclusion and what would change it. Do not restate the per-vendor split, totals or line counts already on the card or table.
- Write money the way an Indian buyer reads it: ₹3.70 crore, ₹2.13 lakh, ₹4.00 per piece. Never print full rupee-and-paise amounts like ₹3,70,39,800.85.
- Counts matter too: if you say "N lines" or "N vendors", N must come from a query result (use COUNT), never from your own counting.
- Each turn stands alone: re-query any number you repeat from earlier in the conversation, because the buyer may have changed the data since.

Style: lead with the answer in one or two sentences. Then only what changes the decision. Aim for about 60 words and at most one table, chart or scenario card, unless the buyer asks for detail; the table or card carries the detail. Do not walk through individual flags or unit conversions unless asked. Refer to vendors by short name, not id. Indian number formatting: lakh and crore for large rupee amounts are fine if you also got the exact figure from a tool. No preamble, no restating the question, no generic procurement advice.

Database (SQLite, read-only):
lines(line_id, category, description, board_spec, uom, annual_qty, est_weight_g, last_year_price_inr, last_year_value_inr)
vendors(vendor, vendor_name, incumbent, responded, lines_quoted, quality_status, freight, freight_pct, held_back_from_award, payment_terms, lead_time_days, validity, quoted_currency, discount_pct, discount_condition, discount_applied, vendor_wide_assumptions, open_issues)
quotes(vendor, vendor_name, line_id, unit_price_inr, annual_value_inr, status, cell_status, award_eligible, flags, as_quoted, spec_deviation)   -- one row per usable price
not_quoted(vendor, vendor_name, line_id, reason)   -- lines a vendor did not price, or priced unusably
questionnaire(vendor, vendor_name, question_id, question, mandatory, answer, result, note)   -- result: pass | fail | unknown | info
documents(vendor, vendor_name, filename, doc_type, summary, valid_until)
vendor is the short name (join and filter on it); vendor_name is the full legal name.
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


HONORIFIC = {"shree", "shri", "sri", "the", "new", "kraft", "paper", "board", "boxes", "green"}   # too common to identify a vendor alone


def vendor_aliases(comp):
    """id -> lowercase strings that name that vendor in prose or SQL."""
    out = {}
    for v in comp["vendors"]:
        al = {v["name"].lower(), v["short"].lower()}
        words = v["short"].split()
        if len(words) > 1:
            al.update(w.lower() for w in words if len(w) >= 5 and w.lower() not in HONORIFIC)
        out[v["id"]] = al
    return out


def _named(text, aliases):
    t = re.sub(r"[_\s]+", " ", (text or "").lower())
    return {vid for vid, al in aliases.items() if any(re.search(r"(?<![a-z0-9])" + re.escape(a) + r"(?![a-z0-9])", t) for a in al)}


def _strings(obj, acc):
    if isinstance(obj, str):
        acc.append(obj)
    elif isinstance(obj, dict):
        for v in obj.values():
            _strings(v, acc)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            _strings(v, acc)
    return acc


TRAILER = re.compile(r"\s*\[Tools used\b.*\Z", re.S | re.I)


PRICEY = re.compile(r"₹|\bRs\.?|\bINR\b|\b(?:cheap\w*|costl\w*|expensive|price[sd]?|pricing|cost[s]?|saving[s]?|lakh|crore|lowest|highest|undercut\w*|value)\b", re.I)


def _initials(comp):
    """'sb' -> Shree Balaji: the abbreviations models use for table aliases."""
    out = {}
    for v in comp["vendors"]:
        w = v["short"].lower().split()
        if len(w) > 1:
            out.setdefault("".join(x[0] for x in w), set()).add(v["id"])
    return {k: next(iter(s)) for k, s in out.items() if len(s) == 1}


def _has_number(obj):
    if isinstance(obj, bool):
        return False
    if isinstance(obj, (int, float)):
        return True
    if isinstance(obj, dict):
        return any(_has_number(v) for v in obj.values())
    if isinstance(obj, (list, tuple)):
        return any(_has_number(v) for v in obj)
    return False


def check_vendors(text, evidence, comp):
    return [d["text"] for d in vendor_checks(text, evidence, comp)]


def vendor_checks(text, evidence, comp):
    """Figures can trace and still belong to the wrong vendor. Checks on this turn only:
    1. labels: a query that filters on vendor literals but labels its output with another vendor (a comment, a column or
       table alias, a THEN label) is flagged;
    2. prose: a vendor named next to prices must be one that a query or scenario returning numbers filtered on or
       returned. Naming it is fine if it only came from a roster or quality query and nothing narrower was run, and
       non-price statements ("failed quality", "held back") are not checked against price queries.
    evidence: [{"sql": str|None, "result": any}] for each tool call of the turn."""
    aliases, ids, inits = vendor_aliases(comp), {v["id"].lower(): v["id"] for v in comp["vendors"]}, _initials(comp)
    label = {v["id"]: v["short"] for v in comp["vendors"]}
    lit_vendors = lambda s: ({ids[s.lower()]} if s.lower() in ids else set()) | _named(s, aliases)
    numeric, other, filters, flagged = set(), set(), [], []
    for e in evidence:
        sql = e.get("sql") or ""
        code = re.sub(r"/\*.*?\*/", " ", re.sub(r"--[^\n]*", " ", sql), flags=re.S)
        filt, then = set(), set()                        # literals after THEN/ELSE are labels the query prints, not filters
        for kw, lit in re.findall(r"(\bTHEN\s+|\bELSE\s+)?'((?:[^']|'')*)'", code, re.I):
            (then if kw else filt).update(lit_vendors(lit))
        ret = set()
        for st in _strings(e.get("result"), []):
            ret |= lit_vendors(st.strip())
        has_num = e.get("sql") is None or _has_number(e.get("result"))
        (numeric if has_num else other).update(filt | ret)
        if has_num and filt and e.get("sql"):
            filters.append(filt)
        if not sql:
            continue
        # what the query calls its parts: comments, column aliases, THEN labels, table aliases
        labels = set(then)
        for c in re.findall(r"--([^\n]*)", sql) + re.findall(r"/\*(.*?)\*/", sql, re.S):
            labels |= _named(c, aliases) | {ids[m.lower()] for m in re.findall(r"\b(V\d+)\b", c) if m.lower() in ids}
        for a in re.findall(r"\bAS\s+[\"'`]?([A-Za-z_][\w]*)", code, re.I):
            labels |= _named(a, aliases)
        bound = {}                                       # table alias -> vendor it is filtered on
        for al, lit in re.findall(r"\b(\w+)\.vendor(?:_name|_id)?\s*=\s*'([^']*)'", code, re.I):
            bound.setdefault(al.lower(), set()).update(lit_vendors(lit))
        for al, vs in bound.items():
            meant = _named(al, aliases) | {inits[p] for p in al.split("_") if p in inits}
            if meant and vs and not (meant & vs):
                flagged.append({"kind": "mixup", "vendors": [label[m] for m in sorted(meant)] + [label[v] for v in sorted(vs)],
                                "text": f"A query names table alias {al} after {' / '.join(label[m] for m in sorted(meant))} "
                                        f"but filters it on {' / '.join(label[v] for v in sorted(vs))}."})
        if filt and labels - filt:
            flagged.append({"kind": "mixup", "vendors": [label[v] for v in sorted(labels - filt)] + [label[v] for v in sorted(filt)],
                            "text": f"A query filters on {' / '.join(label[v] for v in sorted(filt))} but labels its result "
                                    f"{' / '.join(label[v] for v in sorted(labels - filt))}."})
    issues = list({d["text"]: d for d in flagged}.values())
    for vid in sorted(_named(text, aliases) - numeric - other):
        issues.append({"kind": "unreferenced", "vendors": [label[vid]],
                       "text": f"{label[vid]} is named in the answer, but no query, result or scenario in this turn refers to {label[vid]}."})
    priced = set()
    for sentence in re.split(r"(?<=[.!?;])\s+|\n+", text or ""):
        if PRICEY.search(sentence):
            priced |= _named(sentence, aliases)
    for vid in sorted((priced & (numeric | other)) - numeric):
        if any(vid not in f for f in filters):
            issues.append({"kind": "mixup", "vendors": [label[vid]] + sorted({label[v] for f in filters for v in f}),
                           "text": f"{label[vid]} is named next to prices, but the queries that returned prices this turn were filtered on "
                                   f"{' / '.join(sorted({label[v] for f in filters for v in f}))}, not {label[vid]}."})
    return issues


def ask(question):
    comp = normalize.build()
    if not comp:
        return {"text": "There is no RFx yet.", "blocks": [], "trace": [], "figures": []}
    snap = snapshot(comp)
    responded = [v for v in comp["vendors"] if v["response_status"] == "extracted"]
    blocks, trace, evidence = [], [], []
    seen = {float(len(comp["rfx"]["lines"])), float(len(comp["vendors"])), float(len(responded))}
    state = {"scenario": None}

    def execute(name, a):
        if name == "query":
            sql = a["sql"].strip().rstrip(";")
            if not re.match(r"(?is)^\s*(select|with)\b", sql):
                return {"error": "Only SELECT statements are allowed."}
            cols, rows = _rows(snap.execute(sql))
            _numbers(rows, seen); seen.add(float(len(rows)))
            evidence.append({"sql": sql, "result": rows})
            trace.append({"tool": "query", "title": a.get("title"), "sql": sql, "rows": len(rows)})
            if a.get("show"):
                blocks.append({"type": "table", "title": a.get("title"), "columns": cols, "rows": rows[:200], "sql": sql})
            return {"columns": cols, "rows": rows, "row_count": len(rows)}
        if name == "award_scenario":
            statuses = [x for x in (a.get("allowed_statuses") or []) if x != "needs_review"] or None   # enforced, not just prompted
            res = award_scenario(comp, a.get("eligible_vendors") or a.get("eligible_vendor_ids"), a.get("max_vendors"), statuses)
            if "error" in res:
                return res
            state["scenario"] = res
            evidence.append({"sql": None, "result": res})
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
            evidence.append({"sql": sql, "result": rows})
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
                        ["Value on needs-review prices (INR)", sc["value_on_needs_review_prices_inr"]],
                        ["Unconstrained split, all vendors (INR)", sc["unconstrained_split_total_inr"]],
                        ["Cost of the constraints (INR)", sc["cost_of_constraints_inr"]],
                        *[[f"Split: {d['vendor_name']}", f"{d['lines']} lines, INR {d['annual_value_inr']:,.2f} ({d['share_pct']}%)"] for d in sc["by_vendor"]],
                        *[[f"If added back: {d['vendor_name']}", f"unknown: {d['not_computable']}" if d.get("not_computable") else d["saving_if_added_inr"]]
                          for d in sc["if_one_excluded_vendor_were_added"]],
                        ["Held back from winning lines", "; ".join(f"{h['vendor_name']} {h['line_id']} ({h['why']})" for h in sc["prices_held_back_pending_review"]) or "none"]])
                else:
                    cols, rows = _rows(snap.execute(s["sql"].strip().rstrip(";")), 5000)
                    evidence.append({"sql": s["sql"], "result": rows})
                    sheet(s["name"], cols, rows)
            short = {v["id"]: v["short"] for v in comp["vendors"]}
            flags = [[short[v["id"]], "(all priced lines)", "assumed", w["code"], w["text"]] for v in comp["vendors"] for w in v.get("wide_assumptions", [])]
            flags += [[short[v["id"]], "(all lines)", f["severity"], f["code"], f["text"]] for v in comp["vendors"] for f in v["flags"]]
            flags += [[short[x["vendor_id"]], x["line_id"], x["status"], f["code"] + (" (vendor-wide)" if f.get("vendor_wide") else ""), f["text"]]
                      for x in comp["cells"] for f in x["flags"]]
            sheet("Assumptions and flags", ["Vendor", "Line", "Status", "Code", "What to know"], flags)
            name_ = re.sub(r"[^A-Za-z0-9_\-]", "_", a["filename"].rsplit(".", 1)[0])[:60] + f"_{int(time.time())}.xlsx"
            db.EXPORTS.mkdir(parents=True, exist_ok=True)
            wb.save(db.EXPORTS / name_)
            trace.append({"tool": "export_xlsx", "title": name_})
            blocks.append({"type": "file", "name": name_, "url": f"/exports/{name_}"})
            return {"ok": True, "file": name_}
        return {"error": f"Unknown tool {name}"}

    history = db.get("analyst_chat", [])
    # The assistant's own turns go back to the model as prose only. What tools earlier turns used is context, so it
    # goes in the system prompt; put in the assistant's mouth, the model learns to write it into its answers.
    model_history = [{"role": h["role"], "text": TRAILER.sub("", h["text"])} for h in history[-12:]]
    model_history.append({"role": "user", "text": question})
    earlier = [h for h in history[-12:] if h["role"] == "assistant" and h.get("trace")][-2:]
    rfx, st = comp["rfx"], comp["settings"]
    roster = "; ".join(f"{v['short']} = {v['name']}" + (" (incumbent)" if v["incumbent"] else "")
                       + (", quotation read" if v["response_status"] == "extracted" else ", no quotation read")
                       + (f", {v['lines_quoted']} of {len(rfx['lines'])} lines priced" if v["response_status"] == "extracted" else "")
                       + (", HELD BACK from award: freight extra, no amount" if v.get("freight_held") else "") for v in comp["vendors"])
    context = (f"\nFacts for this RFx: {rfx['id']}, {len(rfx['lines'])} lines, award date {rfx.get('award_date')}. "
               f"{len(comp['vendors'])} vendors were invited and {len(responded)} responded; {len(responded)} quotations have been read. "
               f"Vendor roster (short name = full name): {roster}. "
               f"Foreign-currency quotes were converted at {json.dumps(st['fx'])} ({st.get('fx_note')}). The buyer can change this rate on the Compare screen.")
    if earlier:
        context += ("\nFor reference only, never quote or imitate this list in an answer: tools behind your earlier answers: " + " || ".join(
            "; ".join(f"{t['tool']}({(t.get('sql') or json.dumps(t.get('args', {})))[:300]})" for t in h["trace"]) for h in earlier))
    llm.begin_turn()
    text = TRAILER.sub("", llm.run_tools(SYSTEM + context, model_history, TOOLS, execute, max_steps=14)).strip()
    figures = trace_figures(text, seen)
    used = llm.used_models()
    turn = {"role": "assistant", "text": text, "blocks": blocks, "trace": trace, "figures": figures,
            "vendor_flags": (vf := vendor_checks(text, evidence, comp)), "vendor_issues": [d["text"] for d in vf],
            "vendors_checked": len(_named(text, vendor_aliases(comp))),
            "model": used[-1] if used else llm.model_name(), "models": used, "primary_model": llm.primary(),
            "fallback": any(m != llm.primary() for m in used)}
    history += [{"role": "user", "text": question}, turn]
    db.put("analyst_chat", history)
    db.audit("analyst", question[:200])
    return turn
