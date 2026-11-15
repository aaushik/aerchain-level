"""Scores the extraction + normalisation pipeline against the ground truth used to fabricate the vendor files.

The number that matters is not accuracy, it is SILENT errors: prices that are wrong and that the product
shows the buyer as trustworthy (verified/assumed) with no flag pointing at the problem.

Run after the five responses have been read:  uv run python scripts/eval.py
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from app import db, normalize  # noqa: E402

TOL = 0.005


def main():
    truth = json.loads((db.SEED / "truth.json").read_text())
    comp = normalize.build()
    if not comp:
        sys.exit("No RFx loaded.")
    cells = {(c["vendor_id"], c["line_id"]): c for c in comp["cells"]}
    lines = [l["id"] for l in comp["rfx"]["lines"]]
    tot = dict(right=0, wrong_flagged=0, wrong_silent=0, missed=0, invented=0, correctly_absent=0)
    print(f"{'vendor':7}{'right':>7}{'wrong, flagged':>16}{'wrong, SILENT':>15}{'missed':>8}{'invented':>10}   by status")
    for v in comp["vendors"]:
        if v["response_status"] != "extracted":
            print(f"{v['id']:7} not read"); continue
        r = dict(right=0, wrong_flagged=0, wrong_silent=0, missed=0, invented=0, correctly_absent=0)
        notes = []
        for lid in lines:
            c, t = cells[(v["id"], lid)], truth.get(v["id"], {}).get(lid)
            # ignore buyer decisions: we are scoring the machine, not the human
            price = c["list_price"] if c["price"] is not None else None      # truth is before discounts and freight, so compare like with like
            if c.get("decision"):
                continue
            if t is None:
                if price is None:
                    r["correctly_absent"] += 1
                else:
                    r["invented"] += 1; notes.append(f"{lid}: invented {price:.2f}")
            elif price is None:
                if c["status"] == "needs_review":
                    r["wrong_flagged"] += 1; notes.append(f"{lid}: no usable price, flagged")
                else:
                    r["missed"] += 1; notes.append(f"{lid}: missed (truth {t:.2f})")
            elif abs(price - t) / t <= TOL:
                r["right"] += 1
            elif c["status"] == "needs_review":
                r["wrong_flagged"] += 1; notes.append(f"{lid}: {price:.2f} vs truth {t:.2f}, flagged {[f['code'] for f in c['flags']]}")
            else:
                r["wrong_silent"] += 1; notes.append(f"{lid}: {price:.2f} vs truth {t:.2f}, SILENT (status {c['status']})")
        for k in tot:
            tot[k] += r[k]
        print(f"{v['id']:7}{r['right']:>7}{r['wrong_flagged']:>16}{r['wrong_silent']:>15}{r['missed']:>8}{r['invented']:>10}   {v['counts']}")
        for n in notes:
            print("         ", n)
    quoted = tot["right"] + tot["wrong_flagged"] + tot["wrong_silent"] + tot["missed"]
    print(f"\nPrices in truth: {quoted}.  Right: {tot['right']} ({100 * tot['right'] / max(quoted, 1):.1f}%).  "
          f"Wrong but flagged for review: {tot['wrong_flagged']}.  Wrong and silent: {tot['wrong_silent']}.  "
          f"Missed: {tot['missed']}.  Invented: {tot['invented']}.")
    print("Note: truth applies each vendor's stated basis at the configured FX rate, before discounts and freight (scored on list price).")


if __name__ == "__main__":
    main()
