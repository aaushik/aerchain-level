"""Turns what vendors wrote into one comparable grid: same lines, same unit, same currency.

Everything here is deterministic arithmetic with a paper trail. Every cell carries:
  raw      what the vendor wrote
  steps    each conversion applied, in words, in order
  flags    every reason this number deserves less than full trust
  status   verified | assumed | needs_review | not_quoted | no_response
  cell_status  the same, ignoring assumptions shared by every price from that vendor (shown once per vendor instead)
A status is never a model's opinion of itself. It is derived from named, checkable reasons.
"""
import json
import re
import statistics
from datetime import date

from . import db
from .spec import weight_basis

SEV = {"verified": 0, "assumed": 1, "needs_review": 2}
GENERIC = {"packaging", "packagings", "industries", "industry", "corrugators", "corrugated", "solutions", "pvt", "ltd", "llp", "private",
           "limited", "(india)", "india", "co", "co.", "&", "and", "enterprises", "boxes"}
# one sentence per code for assumptions that hold for a whole column, where the per-cell text varies by line
WIDE_TEXT = {"WEIGHT": "Quoted per kg. Every price uses a calculated box weight the vendor has not confirmed.",
             "UNIT_INFERRED": "The price basis was inferred from context for every line, not stated next to the prices.",
             "LAST_YEAR": "Every price was filled from last year's contract; none was restated."}


def short_name(name):
    """'Shree Balaji Packaging Industries' -> 'Shree Balaji'. The name buyers, the analyst and SQL use for a vendor."""
    out = []
    for w in (name or "").split():
        if w.lower() in GENERIC or len(out) == 2:
            break
        out.append(w)
    return " ".join(out) or name


def freight_pct(vs, terms):
    """(pct, who) for a vendor whose freight is extra: the buyer's estimate wins over the vendor's stated figure."""
    fr = terms.get("freight") or {}
    if fr.get("status") != "extra":
        return None, None
    if vs.get("freight_pct"):
        return vs["freight_pct"], "buyer"
    p = fr.get("pct")
    return (float(p), "vendor") if isinstance(p, (int, float)) and not isinstance(p, bool) and p > 0 else (None, None)


def inr(x):
    if x is None:
        return "n/a"
    return f"₹{x:,.2f}" if abs(x) < 1000 else f"₹{x:,.0f}"


def flag(code, severity, text, suggestion=None, short=None):
    """text: the full reason, for Details and exports. short: one line for a buyer, 15 words or fewer, no codes."""
    f = {"code": code, "severity": severity, "text": text, "short": short or text}
    if suggestion is not None:
        f["suggested_price"] = round(suggestion, 4)
    return f


def _clip(t, words=15):
    w = (t or "").split()
    return " ".join(w[:words]) + ("…" if len(w) > words else "")


# Which reason leads when a cell has several: most severe first, then one that carries a suggested price, then this order.
PRIORITY = ["TOTAL_MISMATCH", "OUTLIER", "NO_PRICE", "UNIT", "FX_MISSING", "NO_REFERENCE", "MATCH", "SPEC", "SCOPE", "EVIDENCE",
            "READ", "DUPLICATE", "LAST_YEAR", "WEIGHT", "UNIT_INFERRED", "DISCOUNT_COND", "FREIGHT_EST", "FX", "PHOTO"]
# What a group of cells with the same leading reason is about, for group actions and the audit log.
CAUSE = {"TOTAL_MISMATCH": "Rate and line total disagree", "OUTLIER": "Price far from the other quotes", "NO_PRICE": "Unreadable price",
         "UNIT": "Unit cannot be converted", "FX_MISSING": "No exchange rate", "NO_REFERENCE": "No last-year price to refer to",
         "MATCH": "Uncertain line match", "SPEC": "Different specification", "SCOPE": "Unclear what the group rate covers",
         "EVIDENCE": "Cited text not found", "READ": "Hard-to-read digits", "DUPLICATE": "Several items on one line",
         "LAST_YEAR": "Last year's price, not restated", "WEIGHT": "Calculated weight on a per-kg rate", "UNIT_INFERRED": "Price basis inferred",
         "DISCOUNT_COND": "Conditional discount applied", "FREIGHT_EST": "Your freight estimate", "FX": "Currency conversion", "PHOTO": "Photo reading"}


def headline(flags):
    """The one reason a buyer sees first. Cell-specific reasons beat column-wide ones; a unit error caught twice is one sentence."""
    if not flags:
        return None
    pool = [f for f in flags if not f.get("vendor_wide")] or flags
    rank = lambda f: (-SEV[f["severity"]], "suggested_price" not in f, PRIORITY.index(f["code"]) if f["code"] in PRIORITY else 99)
    top = sorted(pool, key=rank)[0]
    text = top["short"]
    out = next((f for f in pool if f["code"] == "OUTLIER"), None)
    if top["code"] == "TOTAL_MISMATCH" and out:
        pct = re.search(r"is (\d+% (?:below|above))", out["short"])
        text = text.rstrip(".") + (f", and the price is {pct.group(1)} the other quotes." if pct else ".")
    return {"code": top["code"], "severity": top["severity"], "text": text, "suggested_price": top.get("suggested_price"),
            "cause": CAUSE.get(top["code"], top["code"].lower().replace("_", " ")), "vendor_wide": bool(top.get("vendor_wide"))}


DIM = re.compile(r"(\d+(?:\.\d+)?)\s*[x×*]\s*(\d+(?:\.\d+)?)(?:\s*[x×*]\s*(\d+(?:\.\d+)?))?", re.I)


def _dims(line):
    return [x for x in (line.get("length_mm"), line.get("width_mm"), line.get("height_mm")) if x]


def _fmt(d):
    return " × ".join(f"{x:.0f}" for x in d) + " mm"


def match_check(it, line, lines):
    """Code's opinion of the line match, from the vendor's own item description.
    'confirmed': dimensions (and ply, where the vendor states it) equal this line and no other line.
    (vendor dims, line dims): the dimensions contradict the chosen line. None: nothing parseable, keep the model's view."""
    if line.get("kind") in ("partition", "roll"):
        return None
    desc = (it.get("vendor_description") or "").lower()
    m = DIM.search(desc)
    target = _dims(line)
    if not m or not target:
        return None
    d = [float(g) for g in m.groups() if g]
    if '"' in desc or re.search(r"\binch|\bin\b", desc):
        d = [x * 25.4 for x in d]
    if len(d) != len(target):
        return None
    eq = lambda a, b: len(a) == len(b) and all(abs(x - y) <= max(3, 0.015 * y) for x, y in zip(sorted(a), sorted(b)))
    if not eq(d, target):
        return (_fmt(d), _fmt(target))
    ply = re.search(r"(\d)\s*-?\s*ply", desc)
    ply = int(ply.group(1)) if ply else None
    if ply is not None and line.get("ply") and ply != line["ply"]:
        return None                                      # a ply difference is a spec question, not a matching one
    same = [l for l in lines if l.get("kind") not in ("partition", "roll") and eq(d, _dims(l)) and (ply is None or l.get("ply") == ply)]
    return "confirmed" if [l["id"] for l in same] == [line["id"]] else None


def vendor_settings(settings, vid, terms):
    vs = dict(settings.get("vendors", {}).get(vid, {}))
    discounts = terms.get("discounts") or []
    if "apply_discount" not in vs:
        # unconditional discounts are real prices; conditional ones are a negotiation outcome, not a quote
        vs["apply_discount"] = any(not d.get("condition") for d in discounts)
    vs.setdefault("freight_pct", None)
    return vs


def normalize_item(it, line, rfx, settings, vs, terms):
    steps, flags = [], []
    cur, amount, per = it["currency"], it["amount"], it["per_qty"] or 1
    uom = it["uom"]
    raw = {"amount": amount, "currency": cur, "per_qty": per, "uom": uom, "uom_text": it.get("uom_text"),
           "reference": it.get("price_reference")}

    # 1. prices the vendor pointed at instead of stating
    if it.get("price_reference") == "same_as_last_year":
        ly = rfx.get("last_year") or {}
        if line.get("last_year_price") is not None and ly.get("vendor_id") == it["_vendor_id"]:
            amount, cur, per, uom = line["last_year_price"], rfx.get("currency", "INR"), 1, line["uom"]
            steps.append(f"Vendor wrote \"same as last year\". Took {inr(amount)} per {line['uom']} from contract {ly.get('contract_ref')}.")
            flags.append(flag("LAST_YEAR", "assumed", short=f"Not stated by the vendor; filled from last year's price ({inr(amount)}).", text=f"Price not stated by the vendor. Filled from last year's contract "
                              f"{ly.get('contract_ref')} ({inr(amount)}). Get it restated in writing before award."))
        else:
            flags.append(flag("NO_REFERENCE", "needs_review", short="Vendor said \"same as last year\", but there is no last-year price.", text="Vendor referred to last year's price but there is no prior contract "
                              "with this vendor for this line."))
            return None, raw, steps, flags
    if amount is None:
        flags.append(flag("NO_PRICE", "needs_review", short="A price is mentioned but could not be read.", text="A price is mentioned for this line but could not be read."))
        return None, raw, steps, flags

    price = amount / per
    if per != 1:
        steps.append(f"{cur} {amount:,.2f} per {per:g} {uom} → {cur} {price:,.4f} per {uom}.")
    else:
        unit_txt = re.sub(r"^\s*per\s+", "", it.get('uom_text') or uom, flags=re.I)   # vendors often write "per box"
        steps.append(f"Vendor quoted {cur} {amount:,.4f} per {unit_txt}.")

    # 2. unit of measure
    if uom != line["uom"]:
        if uom == "kg" and line.get("est_weight_g"):
            w = line["est_weight_g"]
            price = price * w / 1000
            steps.append(f"Per-kg rate × {w} g per {line['uom']} = {cur} {price:,.4f} per {line['uom']}.")
            flags.append(flag("WEIGHT", "assumed", short=f"Per-kg rate converted with a calculated weight of {w} g the vendor has not confirmed.", text=f"Quoted per kg. Converted with a calculated weight of {w} g per {line['uom']} "
                              f"({weight_basis(line)}). The vendor has not confirmed this weight; a 5% difference in weight is a 5% "
                              "difference in price."))
        else:
            flags.append(flag("UNIT", "needs_review", short=f"Vendor's unit (\"{it.get('uom_text') or uom}\") cannot be converted to per {line['uom']}.", text=f"Vendor's unit (\"{it.get('uom_text') or uom}\") cannot be converted to the "
                              f"RFx unit ({line['uom']}) from the information available."))
            return None, raw, steps, flags
    if not it.get("unit_explicit", True) and it.get("price_reference") != "same_as_last_year":
        flags.append(flag("UNIT_INFERRED", "assumed", short="Price basis inferred from context, not stated next to the price.", text=f"The price basis (\"{it.get('uom_text')}\") was inferred from context, "
                          "not stated next to this price."))

    # 3. currency
    base = rfx.get("currency", "INR")
    if cur != base:
        rate = settings.get("fx", {}).get(cur)
        if not rate:
            flags.append(flag("FX_MISSING", "needs_review", short=f"Quoted in {cur}; no exchange rate set.", text=f"Quoted in {cur}; no exchange rate has been set."))
            return None, raw, steps, flags
        price *= rate
        steps.append(f"{cur} → {base} at {rate:g} = {inr(price)}.")
        flags.append(flag("FX", "assumed", short=f"Converted from {cur} at {rate:g}; the invoice rate may differ.", text=f"Quoted in {cur}. Converted at {rate:g} ({settings.get('fx_note', 'buyer-set rate')}). "
                          "The vendor's invoice rate may differ; every 1% move in the rate moves this price 1%."))
    list_price = price

    # 4. discounts and freight (vendor-level terms applied to every line)
    for d in terms.get("discounts") or []:
        if vs["apply_discount"]:
            price *= (1 - d["pct"] / 100)
            steps.append(f"Less {d['pct']:g}% discount ({d.get('condition') or 'unconditional'}) = {inr(price)}.")
            if d.get("condition"):
                flags.append(flag("DISCOUNT_COND", "assumed", short=f"Includes a conditional {d['pct']:g}% discount you chose to apply.", text=f"{d['pct']:g}% discount applied by buyer's choice. It is conditional: {d['condition']}"))
    fp, who = freight_pct(vs, terms)
    if fp and who == "buyer":
        price *= (1 + fp / 100)
        steps.append(f"Plus {fp:g}% estimated freight (buyer's estimate) = {inr(price)}.")
        flags.append(flag("FREIGHT_EST", "assumed", short=f"Includes your {fp:g}% freight estimate.", text=f"Freight is extra. {fp:g}% added as the buyer's estimate, not the vendor's figure."))
    elif fp:
        price *= (1 + fp / 100)
        steps.append(f"Plus {fp:g}% freight, as stated by the vendor = {inr(price)}.")

    # 5. checks on the reading itself
    if it.get("source_kind") == "image":
        flags.append(flag("PHOTO", "assumed", short="Read from a photograph; compare with the image.", text="Read from a photograph. Compare with the image before relying on it."))
    elif it.get("evidence_verified") is False:
        flags.append(flag("EVIDENCE", "needs_review", short="The cited text was not found in the vendor's document.", text="The text the model cited for this price could not be found in the source document."))
    rc, mc = it.get("read_confidence", "high"), it.get("match_confidence", "high")
    if rc != "high":
        flags.append(flag("READ", "needs_review" if rc == "low" else "assumed", short=f"Digits hard to read; reading confidence is {rc}.", text=
                          f"Reading confidence is {rc}." + (f" {it['note']}" if it.get("note") else "")))
    chk = match_check(it, line, rfx["lines"])
    if chk == "confirmed" and mc != "high":
        steps.append("Line match confirmed: dimensions and ply equal the RFx line.")
    elif isinstance(chk, tuple):
        flags.append(flag("MATCH", "needs_review", short=f"Vendor's item measures {chk[0]}; this line is {chk[1]}.",
                          text=f"The vendor's item (\"{it.get('vendor_description')}\") measures {chk[0]}, but this RFx line is {chk[1]}. "
                          "It was probably matched to the wrong line."))
    if mc != "high" and it.get("price_reference") in ("group_rate", "same_as_last_year"):
        flags.append(flag("SCOPE", "needs_review", short="Unclear whether the vendor's group rate covers this line.", text=f"It is not clear that the vendor's statement (\"{it.get('evidence')}\") covers this line."
                          + (f" {it['note']}." if it.get("note") else "") + " Ask the vendor which lines it applies to."))
    elif mc != "high" and chk is None:
        flags.append(flag("MATCH", "needs_review" if mc == "low" else "assumed", short=f"Matched to this line with {mc} confidence; check the vendor's item.", text=
                          f"Matched to this RFx line with {mc} confidence (vendor called it \"{it.get('vendor_description')}\")."))
    if it.get("spec_deviation"):
        flags.append(flag("SPEC", "needs_review", short=_clip(f"Not like-for-like: {it['spec_deviation']}"), text=f"Not like-for-like: {it['spec_deviation']}"))

    # 6. the vendor's own arithmetic is a free second opinion
    tot = it.get("stated_line_total")
    qty = it.get("stated_qty") or line.get("annual_qty")       # the vendor's own quantity if they showed one
    if tot and qty:
        implied = tot / qty
        if cur != base:
            implied *= settings.get("fx", {}).get(cur, 1)
        if list_price and abs(implied - list_price) / max(implied, list_price) > 0.02:
            flags.append(flag("TOTAL_MISMATCH", "needs_review", short=f"Likely a unit error: the vendor's own total implies {inr(implied)}, not {inr(list_price)}.", text=
                              f"The vendor's own line total ({cur} {tot:,.0f} for {qty:,.0f}) implies {inr(implied)} per "
                              f"{line['uom']}, but the unit rate as written gives {inr(list_price)}. One of them is wrong, most likely "
                              "the unit basis.", suggestion=implied))
    return {"price": price, "list_price": list_price}, raw, steps, flags


def evaluate_questionnaire(rfx, ext):
    award = date.fromisoformat(rfx.get("award_date") or date.today().isoformat())
    answers = {a["question_id"]: a for a in (ext or {}).get("questionnaire", [])}
    docs = (ext or {}).get("documents", [])
    out = []
    for q in rfx.get("questionnaire", []):
        a = answers.get(q["id"])
        r = {"question_id": q["id"], "question": q["text"], "mandatory": q.get("mandatory", False), "pass_text": q.get("pass_text"),
             "answer": a.get("answer_text") if a else None, "value": a.get("value") if a else None,
             "evidence": a.get("evidence") if a else None, "source_file": a.get("source_file") if a else None,
             "evidence_verified": a.get("evidence_verified") if a else None, "result": "unknown", "note": None}
        rule = q.get("rule")
        if not a:
            r["note"] = "Not answered."
        elif not rule:
            r["result"] = "info"
        else:
            v = a.get("value")
            k = rule["kind"]
            if k in ("yes", "yes_and_valid"):
                if v == "no":
                    r["result"] = "fail"
                elif v == "yes":
                    r["result"] = "pass"
                    if k == "yes_and_valid":
                        cert = next((d for d in docs if d.get("doc_type") == "iso_9001_certificate"), None)
                        until = (cert or {}).get("valid_until") or a.get("valid_until")
                        if until:
                            try:
                                if date.fromisoformat(until) < award:
                                    r["result"] = "fail"
                                    r["note"] = (f"Vendor answered yes, but the {'attached certificate' if cert and cert.get('valid_until') else 'stated validity'} "
                                                 f"ended on {until}, before the award date {award.isoformat()}.")
                                elif cert:
                                    r["note"] = f"Valid until {until} (attached certificate)."
                                else:                   # the question asks for a copy: a claim alone does not clear a vendor
                                    r["result"] = "unknown"; r["missing"] = "certificate_copy"
                                    r["note"] = f"Vendor states it is valid until {until}, but no certificate copy is attached."
                            except ValueError:
                                r["result"] = "unknown"; r["note"] = "Validity date could not be read."
                        else:
                            r["result"] = "unknown"; r["note"] = "Answered yes, but no validity date is evidenced."
                            if not cert:
                                r["missing"] = "certificate_copy"; r["note"] = "Answered yes, but no certificate copy is attached and no validity date is stated."
            elif k in ("max", "min"):
                if isinstance(v, (int, float)):
                    ok = v <= rule["value"] if k == "max" else v >= rule["value"]
                    r["result"] = "pass" if ok else "fail"
                    if not ok:
                        r["note"] = f"{v:g} is {'above' if k == 'max' else 'below'} the threshold of {rule['value']:g}."
                else:
                    r["note"] = "Answer is not a number."
        if a and a.get("evidence_verified") is False:
            r["note"] = ((r["note"] or "") + " Cited evidence not found in source.").strip()
        out.append(r)
    mand = [r for r in out if r["mandatory"]]
    if any(r["result"] == "fail" for r in mand):
        overall = "failed"
    elif mand and all(r["result"] == "pass" for r in mand):
        overall = "cleared"
    else:
        overall = "incomplete"
    return out, overall


def build(rfx=None):
    """The whole comparison, computed fresh from extractions + buyer decisions + settings."""
    rfx = rfx or db.get("rfx")
    if not rfx:
        return None
    settings = db.settings()
    resp = {r["vendor_id"]: r for r in db.rows("SELECT * FROM responses")}
    ov = {(o["vendor_id"], o["line_id"]): o for o in db.rows("SELECT * FROM overrides")}
    relevel = db.get("relevel", {})
    lines = {l["id"]: l for l in rfx["lines"]}
    cells, vendors = {}, []

    for v in rfx["vendors"]:
        vid = v["id"]
        r = resp.get(vid)
        ext = json.loads(r["extraction"]) if r and r["extraction"] else None
        terms = (ext or {}).get("terms") or {}
        vs = vendor_settings(settings, vid, terms)
        vflags = []
        by_line = {}
        unmatched = []
        for it in (ext or {}).get("items", []):
            it["_vendor_id"] = vid
            if it["line_id"]:
                by_line.setdefault(it["line_id"], []).append(it)
            else:
                unmatched.append(it)
        nq = {n["line_id"]: n for n in (ext or {}).get("not_quoted", [])}

        for lid, line in lines.items():
            c = {"vendor_id": vid, "line_id": lid, "price": None, "list_price": None, "annual_value": None, "raw": None,
                 "steps": [], "flags": [], "evidence": None, "status": "no_response" if not ext else "not_quoted",
                 "reason": None, "decision": None}
            its = by_line.get(lid)
            if its:
                it = its[0]
                res, raw, steps, flags = normalize_item(it, line, rfx, settings, vs, terms)
                if len(its) > 1:
                    flags.append(flag("DUPLICATE", "needs_review", short="Several vendor items matched this line; the first is used.", text=f"{len(its)} different vendor items were matched to this line; using the first."))
                c.update(raw=raw, steps=steps, flags=flags,
                         evidence={"quote": it.get("evidence"), "location": it.get("location"), "source_file": it.get("source_file"),
                                   "source_kind": it.get("source_kind"), "verified": it.get("evidence_verified"),
                                   "vendor_description": it.get("vendor_description"), "note": it.get("note")})
                if res:
                    c.update(price=res["price"], list_price=res["list_price"])
                c["status"] = "needs_review"
            elif lid in nq:
                c["reason"] = nq[lid].get("reason") or "Vendor did not quote this line."
            elif ext:
                c["reason"] = "No price found for this line in the vendor's response."
            cells[(vid, lid)] = c

        # vendor-level terms that change comparability
        fr = terms.get("freight") or {}
        held = bool(ext and fr.get("status") == "extra" and not freight_pct(vs, terms)[0])
        if held:
            vflags.append(flag("FREIGHT", "needs_review", short="Freight extra with no amount: prices held back from awards.", text="Freight is extra and no amount is stated. These prices are ex-works, the others "
                               "are delivered, so they are held back from award scenarios. Set a freight estimate or ask the vendor."))
        if ext and fr.get("status") == "unclear":
            vflags.append(flag("FREIGHT_UNCLEAR", "assumed", short="Unclear whether freight is included.", text="The response does not say whether freight is included."))
        for d in terms.get("discounts") or []:
            if d.get("condition") and not vs["apply_discount"]:
                vflags.append(flag("DISCOUNT_OFF", "assumed", short=f"Conditional {d['pct']:g}% discount offered, not applied.", text=f"A {d['pct']:g}% discount is offered but NOT applied because it is conditional: "
                                   f"{d['condition']}"))
        for ch in terms.get("one_time_charges") or []:
            vflags.append(flag("ONE_TIME", "assumed", short=_clip(f"One-time charge outside unit prices: {ch.get('description')}"), text=f"One-time charge outside unit prices: {ch.get('description')} "
                               f"({ch.get('currency', '')} {ch.get('amount')})."))
        for it in unmatched:
            vflags.append(flag("UNMATCHED", "needs_review", short=_clip(f"Item not matched to any line: {it.get('vendor_description')}"), text=f"Vendor item \"{it.get('vendor_description')}\" could not be matched to any RFx line."))
        qa, qstatus = evaluate_questionnaire(rfx, ext)
        vendors.append({"id": vid, "name": v["name"], "short": short_name(v["name"]), "freight_held": held,
                        "freight_pct": freight_pct(vs, terms), "city": v.get("city"), "incumbent": v.get("incumbent", False),
                        "response_status": r["status"] if r else "awaiting", "error": r["error"] if r else None,
                        "email": json.loads(r["email"]) if r else None, "model": r["model"] if r else None,
                        "terms": terms, "settings": vs, "flags": vflags, "questionnaire": qa, "quality": qstatus if ext else "no_response",
                        "documents": (ext or {}).get("documents", []), "ambiguities": (ext or {}).get("ambiguities", []),
                        "files": (ext or {}).get("files", []), "quote_ref": (ext or {}).get("quote_ref")})

    # cross-vendor outlier check: a price far from everyone else's is usually a unit error, not a bargain
    for lid in lines:
        priced = {vid: c["price"] for (vid, l), c in cells.items() if l == lid and c["price"]}
        for vid, p in priced.items():
            others = [x for k, x in priced.items() if k != vid]
            if len(others) >= 2:
                med = statistics.median(others)
                if p < 0.5 * med or p > 2 * med:
                    cells[(vid, lid)]["flags"].append(flag(
                        "OUTLIER", "needs_review", short=f"{inr(p)} is {abs(p / med - 1) * 100:.0f}% {'below' if p < med else 'above'} the other quotes; likely a unit error.",
                        text=f"{inr(p)} is {abs(p / med - 1) * 100:.0f}% {'below' if p < med else 'above'} the median of the other quotes "
                        f"({inr(med)}). Likely a unit or decimal error rather than a real price."))

    # An assumption carried by every priced cell of a vendor (an FX rate, a photo) says nothing about any one cell.
    # It is marked vendor_wide and shown once on the column; status still counts it, cell_status does not.
    for v in vendors:
        priced = [c for (vid, _), c in cells.items() if vid == v["id"] and c["price"] is not None]
        codes = set.intersection(*[{f["code"] for f in c["flags"] if f["severity"] == "assumed"} for c in priced]) if len(priced) >= 2 else set()
        v["wide_assumptions"] = []
        for code in sorted(codes):
            texts = {f["text"] for c in priced for f in c["flags"] if f["code"] == code}
            shorts = {f["short"] for c in priced for f in c["flags"] if f["code"] == code}
            v["wide_assumptions"].append({"code": code, "text": texts.pop() if len(texts) == 1 else WIDE_TEXT.get(code, next(iter(texts))),
                                          "short": shorts.pop() if len(shorts) == 1 else WIDE_TEXT.get(code, next(iter(shorts)))})
            for c in priced:
                for f in c["flags"]:
                    if f["code"] == code:
                        f["vendor_wide"] = True

    # status from flags, then buyer decisions on top
    worst = lambda fl: [k for k, s in SEV.items() if s == max([SEV[f["severity"]] for f in fl], default=0)][0]
    for (vid, lid), c in cells.items():
        line = lines[lid]
        if c["raw"] is not None:
            c["machine_status"] = c["status"] = worst(c["flags"])
            c["cell_status"] = worst([f for f in c["flags"] if not f.get("vendor_wide")])
            if c["price"] is None:
                c["machine_status"] = c["status"] = c["cell_status"] = "needs_review"
        o = ov.get((vid, lid))
        if o:
            c["decision"] = {"action": o["action"], "price": o["price"], "note": o["note"], "ts": o["ts"], "grp": o.get("grp")}
            if o["action"] == "confirm" and c["price"] is not None:
                c["status"] = "verified"; c["steps"].append("Confirmed by buyer.")
            elif o["action"] == "set_price" and o["price"] is not None:
                c["price"] = o["price"]; c["status"] = "verified"
                c["steps"].append(f"Buyer set the price to {inr(o['price'])}" + (f" ({o['note']})." if o["note"] else "."))
            elif o["action"] == "exclude":
                c["price"] = None; c["status"] = "not_quoted"; c["reason"] = "Excluded by buyer" + (f": {o['note']}" if o["note"] else ".")
            if o["action"] in ("confirm", "set_price", "exclude") and c["status"] in ("verified", "not_quoted"):
                c["cell_status"] = c["status"]
        c.setdefault("cell_status", c["status"])
        c["headline"] = headline(c["flags"])
        if c["price"] is not None:
            c["annual_value"] = c["price"] * line["annual_qty"]
        ch = (relevel.get(vid) or {}).get("changed", {}).get(lid)
        if ch:
            c["changed_by_reply"] = {**ch, "ts": relevel[vid]["ts"]}

    for v in vendors:
        vc = [c for (vid, _), c in cells.items() if vid == v["id"]]
        v["lines_quoted"] = sum(1 for c in vc if c["price"] is not None)
        v["total_value"] = sum(c["annual_value"] or 0 for c in vc)
        v["counts"] = {s: sum(1 for c in vc if c["status"] == s) for s in ("verified", "assumed", "needs_review", "not_quoted")}
        # what the column looks like: white cells that rest only on a column-wide assumption are counted apart, not as verified
        v["cell_counts"] = {s: sum(1 for c in vc if c["cell_status"] == s and not (s == "verified" and c["status"] != "verified"))
                            for s in ("verified", "assumed", "needs_review", "not_quoted")}
        v["cell_counts"]["vendor_wide"] = sum(1 for c in vc if c["cell_status"] == "verified" and c["status"] == "assumed")
        v["relevel"] = relevel.get(v["id"])
        # open causes: what a buyer would have to resolve, each counted once however many lines it touches
        causes = [{"kind": "vendor", "code": f["code"], "severity": f["severity"], "short": f["short"], "text": f["text"]} for f in v["flags"]]
        causes += [{"kind": "column", "code": w["code"], "severity": "assumed", "short": "Every price: " + w["short"][0].lower() + w["short"][1:], "text": w["text"],
                    "lines": [c["line_id"] for c in vc if c["price"] is not None and not c.get("decision")]} for w in v.get("wide_assumptions", [])]
        groups = {}
        for c in vc:
            h = c.get("headline")
            if h and not c.get("decision") and c["cell_status"] in ("assumed", "needs_review") and c["raw"] is not None:
                g = groups.setdefault(h["code"], {"kind": "cells", "code": h["code"], "severity": h["severity"], "short": h["cause"], "lines": [], "suggested": 0})
                g["lines"].append(c["line_id"])
                g["suggested"] += h.get("suggested_price") is not None
                if SEV[h["severity"]] > SEV[g["severity"]]:
                    g["severity"] = h["severity"]
        causes += sorted(groups.values(), key=lambda g: (-SEV[g["severity"]], -len(g["lines"])))
        openq = [q for q in v["questionnaire"] if q["mandatory"] and q["result"] == "unknown"] if v["response_status"] == "extracted" else []
        if openq:                                       # one issue for the questionnaire, the questions listed inside it
            nmiss = sum(1 for q in openq if q.get("missing"))
            causes.append({"kind": "questions", "code": "QUESTIONS", "severity": "needs_review",
                           "short": f"{len(openq)} mandatory question{'s' if len(openq) > 1 else ''} "
                                    + ("not answered" if not nmiss else "still open" if nmiss < len(openq) else "answered without the document asked for"),
                           "items": [("Certificate claimed, copy not attached: " if q.get("missing") else "") + q["question"] for q in openq]})
        v["causes"] = causes

    held = {v["id"] for v in vendors if v["freight_held"]}
    best = {}
    for lid in lines:
        priced = [(c["price"], vid) for (vid, l), c in cells.items()
                  if l == lid and c["price"] is not None and c["status"] != "needs_review" and vid not in held]
        if priced:
            best[lid] = min(priced)[1]
    return {"rfx": rfx, "settings": settings, "vendors": vendors, "best": best,
            "cells": [dict(c) for c in cells.values()]}
