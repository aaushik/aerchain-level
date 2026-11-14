"""Turns what vendors wrote into one comparable grid: same lines, same unit, same currency.

Everything here is deterministic arithmetic with a paper trail. Every cell carries:
  raw      what the vendor wrote
  steps    each conversion applied, in words, in order
  flags    every reason this number deserves less than full trust
  status   verified | assumed | needs_review | not_quoted | no_response
A status is never a model's opinion of itself. It is derived from named, checkable reasons.
"""
import json
import statistics
from datetime import date

from . import db
from .spec import weight_basis

SEV = {"verified": 0, "assumed": 1, "needs_review": 2}


def inr(x):
    if x is None:
        return "n/a"
    return f"₹{x:,.2f}" if abs(x) < 1000 else f"₹{x:,.0f}"


def flag(code, severity, text, suggestion=None):
    f = {"code": code, "severity": severity, "text": text}
    if suggestion is not None:
        f["suggested_price"] = round(suggestion, 4)
    return f


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
            flags.append(flag("LAST_YEAR", "assumed", f"Price not stated by the vendor. Filled from last year's contract "
                              f"{ly.get('contract_ref')} ({inr(amount)}). Get it restated in writing before award."))
        else:
            flags.append(flag("NO_REFERENCE", "needs_review", "Vendor referred to last year's price but there is no prior contract "
                              "with this vendor for this line."))
            return None, raw, steps, flags
    if amount is None:
        flags.append(flag("NO_PRICE", "needs_review", "A price is mentioned for this line but could not be read."))
        return None, raw, steps, flags

    price = amount / per
    if per != 1:
        steps.append(f"{cur} {amount:,.2f} per {per:g} {uom} → {cur} {price:,.4f} per {uom}.")
    else:
        steps.append(f"Vendor quoted {cur} {amount:,.4f} per {it.get('uom_text') or uom}.")

    # 2. unit of measure
    if uom != line["uom"]:
        if uom == "kg" and line.get("est_weight_g"):
            w = line["est_weight_g"]
            price = price * w / 1000
            steps.append(f"Per-kg rate × {w} g per {line['uom']} = {cur} {price:,.4f} per {line['uom']}.")
            flags.append(flag("WEIGHT", "assumed", f"Quoted per kg. Converted with a calculated weight of {w} g per {line['uom']} "
                              f"({weight_basis(line)}). The vendor has not confirmed this weight; a 5% difference in weight is a 5% "
                              "difference in price."))
        else:
            flags.append(flag("UNIT", "needs_review", f"Vendor's unit (\"{it.get('uom_text') or uom}\") cannot be converted to the "
                              f"RFx unit ({line['uom']}) from the information available."))
            return None, raw, steps, flags
    if not it.get("unit_explicit", True) and it.get("price_reference") != "same_as_last_year":
        flags.append(flag("UNIT_INFERRED", "assumed", f"The price basis (\"{it.get('uom_text')}\") was inferred from context, "
                          "not stated next to this price."))

    # 3. currency
    base = rfx.get("currency", "INR")
    if cur != base:
        rate = settings.get("fx", {}).get(cur)
        if not rate:
            flags.append(flag("FX_MISSING", "needs_review", f"Quoted in {cur}; no exchange rate has been set."))
            return None, raw, steps, flags
        price *= rate
        steps.append(f"{cur} → {base} at {rate:g} = {inr(price)}.")
        flags.append(flag("FX", "assumed", f"Quoted in {cur}. Converted at {rate:g} ({settings.get('fx_note', 'buyer-set rate')}). "
                          "The vendor's invoice rate may differ; every 1% move in the rate moves this price 1%."))
    list_price = price

    # 4. discounts and freight (vendor-level terms applied to every line)
    for d in terms.get("discounts") or []:
        if vs["apply_discount"]:
            price *= (1 - d["pct"] / 100)
            steps.append(f"Less {d['pct']:g}% discount ({d.get('condition') or 'unconditional'}) = {inr(price)}.")
            if d.get("condition"):
                flags.append(flag("DISCOUNT_COND", "assumed", f"{d['pct']:g}% discount applied by buyer's choice. It is conditional: {d['condition']}"))
    if (terms.get("freight") or {}).get("status") == "extra" and vs.get("freight_pct"):
        price *= (1 + vs["freight_pct"] / 100)
        steps.append(f"Plus {vs['freight_pct']:g}% estimated freight (buyer's estimate) = {inr(price)}.")
        flags.append(flag("FREIGHT_EST", "assumed", f"Freight is extra. {vs['freight_pct']:g}% added as the buyer's estimate, not the vendor's figure."))

    # 5. checks on the reading itself
    if it.get("source_kind") == "image":
        flags.append(flag("PHOTO", "assumed", "Read from a photograph. Compare with the image before relying on it."))
    elif it.get("evidence_verified") is False:
        flags.append(flag("EVIDENCE", "needs_review", "The text the model cited for this price could not be found in the source document."))
    rc, mc = it.get("read_confidence", "high"), it.get("match_confidence", "high")
    if rc != "high":
        flags.append(flag("READ", "needs_review" if rc == "low" else "assumed",
                          f"Reading confidence is {rc}." + (f" {it['note']}" if it.get("note") else "")))
    if mc != "high" and it.get("price_reference") in ("group_rate", "same_as_last_year"):
        flags.append(flag("SCOPE", "needs_review", f"It is not clear that the vendor's statement (\"{it.get('evidence')}\") covers this line."
                          + (f" {it['note']}." if it.get("note") else "") + " Ask the vendor which lines it applies to."))
    elif mc != "high":
        flags.append(flag("MATCH", "needs_review" if mc == "low" else "assumed",
                          f"Matched to this RFx line with {mc} confidence (vendor called it \"{it.get('vendor_description')}\")."))
    if it.get("spec_deviation"):
        flags.append(flag("SPEC", "needs_review", f"Not like-for-like: {it['spec_deviation']}"))

    # 6. the vendor's own arithmetic is a free second opinion
    tot = it.get("stated_line_total")
    qty = it.get("stated_qty") or line.get("annual_qty")       # the vendor's own quantity if they showed one
    if tot and qty:
        implied = tot / qty
        if cur != base:
            implied *= settings.get("fx", {}).get(cur, 1)
        if list_price and abs(implied - list_price) / max(implied, list_price) > 0.02:
            flags.append(flag("TOTAL_MISMATCH", "needs_review",
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
                                else:
                                    r["note"] = f"Valid until {until}." + ("" if cert else " Certificate copy not attached; date is the vendor's statement.")
                            except ValueError:
                                r["note"] = "Validity date could not be read."
                        else:
                            r["result"] = "unknown"; r["note"] = "Answered yes, but no validity date is evidenced."
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
                    flags.append(flag("DUPLICATE", "needs_review", f"{len(its)} different vendor items were matched to this line; using the first."))
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
        if ext and fr.get("status") == "extra" and not vs.get("freight_pct"):
            vflags.append(flag("FREIGHT", "needs_review", "Freight is extra and no amount is stated. These prices are ex-works, the others "
                               "are delivered. Set a freight estimate or ask the vendor before comparing totals."))
        if ext and fr.get("status") == "unclear":
            vflags.append(flag("FREIGHT_UNCLEAR", "assumed", "The response does not say whether freight is included."))
        for d in terms.get("discounts") or []:
            if d.get("condition") and not vs["apply_discount"]:
                vflags.append(flag("DISCOUNT_OFF", "assumed", f"A {d['pct']:g}% discount is offered but NOT applied because it is conditional: "
                                   f"{d['condition']}"))
        for ch in terms.get("one_time_charges") or []:
            vflags.append(flag("ONE_TIME", "assumed", f"One-time charge outside unit prices: {ch.get('description')} "
                               f"({ch.get('currency', '')} {ch.get('amount')})."))
        for it in unmatched:
            vflags.append(flag("UNMATCHED", "needs_review", f"Vendor item \"{it.get('vendor_description')}\" could not be matched to any RFx line."))
        qa, qstatus = evaluate_questionnaire(rfx, ext)
        vendors.append({"id": vid, "name": v["name"], "city": v.get("city"), "incumbent": v.get("incumbent", False),
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
                        "OUTLIER", "needs_review",
                        f"{inr(p)} is {abs(p / med - 1) * 100:.0f}% {'below' if p < med else 'above'} the median of the other quotes "
                        f"({inr(med)}). Likely a unit or decimal error rather than a real price."))

    # status from flags, then buyer decisions on top
    for (vid, lid), c in cells.items():
        line = lines[lid]
        if c["raw"] is not None:
            worst = max([SEV[f["severity"]] for f in c["flags"]], default=0)
            c["status"] = [k for k, s in SEV.items() if s == worst][0]
            if c["price"] is None:
                c["status"] = "needs_review"
        o = ov.get((vid, lid))
        if o:
            c["decision"] = {"action": o["action"], "price": o["price"], "note": o["note"], "ts": o["ts"]}
            if o["action"] == "confirm" and c["price"] is not None:
                c["status"] = "verified"; c["steps"].append("Confirmed by buyer.")
            elif o["action"] == "set_price" and o["price"] is not None:
                c["price"] = o["price"]; c["status"] = "verified"
                c["steps"].append(f"Buyer set the price to {inr(o['price'])}" + (f" ({o['note']})." if o["note"] else "."))
            elif o["action"] == "exclude":
                c["price"] = None; c["status"] = "not_quoted"; c["reason"] = "Excluded by buyer" + (f": {o['note']}" if o["note"] else ".")
        if c["price"] is not None:
            c["annual_value"] = c["price"] * line["annual_qty"]

    for v in vendors:
        vc = [c for (vid, _), c in cells.items() if vid == v["id"]]
        v["lines_quoted"] = sum(1 for c in vc if c["price"] is not None)
        v["total_value"] = sum(c["annual_value"] or 0 for c in vc)
        v["counts"] = {s: sum(1 for c in vc if c["status"] == s) for s in ("verified", "assumed", "needs_review", "not_quoted")}

    best = {}
    for lid in lines:
        priced = [(c["price"], vid) for (vid, l), c in cells.items() if l == lid and c["price"] is not None and c["status"] != "needs_review"]
        if priced:
            best[lid] = min(priced)[1]
    return {"rfx": rfx, "settings": settings, "vendors": vendors, "best": best,
            "cells": [dict(c) for c in cells.values()]}
