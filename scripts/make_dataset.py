"""Fabricates the demo dataset: one RFx (30 lines), five vendor replies in five shapes.

Writes:
  data/seed/rfx.json            canonical RFx, questionnaire, vendors, last-year prices, FX
  data/seed/truth.json          ground-truth per-piece INR prices (used only by scripts/eval.py)
  data/vendor_replies/<id>/     email.json + attachments, exactly as a buyer would receive them

Nothing here is read by the extraction or analysis code except rfx.json and the raw reply files.
"""
import json
import random
import sys
from pathlib import Path

import numpy as np
from docx import Document
from docx.shared import Pt
from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from PIL import Image, ImageDraw, ImageFilter, ImageFont
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle, PageBreak

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from app.spec import est_weight_g  # noqa: E402
SEED = ROOT / "data" / "seed"
REPLIES = ROOT / "data" / "vendor_replies"
random.seed(11)

FX_USD_INR = 88.50

# ---------------------------------------------------------------- RFx lines
# (id, kind, ply, L, W, H, board spec, print, uom, annual qty)
RAW = [
    ("L01", "rsc", 3, 150, 100, 80, "120/100/120 GSM, 16 BF, B-flute", "Plain", "piece", 420000),
    ("L02", "rsc", 3, 200, 150, 100, "120/100/120 GSM, 16 BF, B-flute", "Plain", "piece", 520000),
    ("L03", "rsc", 3, 250, 200, 100, "120/100/120 GSM, 16 BF, B-flute", "1-colour logo", "piece", 610000),
    ("L04", "rsc", 3, 250, 200, 150, "120/100/120 GSM, 16 BF, B-flute", "1-colour logo", "piece", 480000),
    ("L05", "rsc", 3, 300, 200, 150, "120/100/120 GSM, 16 BF, B-flute", "1-colour logo", "piece", 560000),
    ("L06", "rsc", 3, 300, 250, 200, "120/100/120 GSM, 18 BF, B-flute", "1-colour logo", "piece", 380000),
    ("L07", "rsc", 3, 350, 250, 150, "120/100/120 GSM, 18 BF, B-flute", "1-colour logo", "piece", 300000),
    ("L08", "rsc", 3, 400, 300, 200, "120/100/120 GSM, 18 BF, B-flute", "1-colour logo", "piece", 240000),
    ("L09", "rsc", 3, 450, 300, 250, "140/120/140 GSM, 18 BF, B-flute", "1-colour logo", "piece", 150000),
    ("L10", "rsc", 3, 500, 350, 250, "140/120/140 GSM, 18 BF, B-flute", "1-colour logo", "piece", 110000),
    ("L11", "rsc", 5, 300, 250, 200, "150/120/120/120/150 GSM, 20 BF, BC-flute", "1-colour logo", "piece", 260000),
    ("L12", "rsc", 5, 350, 300, 250, "150/120/120/120/150 GSM, 20 BF, BC-flute", "1-colour logo", "piece", 220000),
    ("L13", "rsc", 5, 400, 300, 300, "150/120/120/120/150 GSM, 20 BF, BC-flute", "1-colour logo", "piece", 190000),
    ("L14", "rsc", 5, 450, 350, 300, "150/120/120/120/150 GSM, 20 BF, BC-flute", "1-colour logo", "piece", 150000),
    ("L15", "rsc", 5, 500, 400, 300, "150/120/120/120/150 GSM, 22 BF, BC-flute", "1-colour logo", "piece", 120000),
    ("L16", "rsc", 5, 600, 400, 400, "150/120/120/120/150 GSM, 22 BF, BC-flute", "1-colour logo", "piece", 80000),
    ("L17", "rsc", 5, 600, 450, 450, "150/120/120/120/150 GSM, 22 BF, BC-flute", "1-colour logo", "piece", 55000),
    ("L18", "rsc", 5, 750, 500, 450, "150/120/120/120/150 GSM, 22 BF, BC-flute", "1-colour logo", "piece", 30000),
    ("L19", "mailer", 3, 200, 150, 50, "E-flute, white top liner 140 GSM", "2-colour outside", "piece", 300000),
    ("L20", "mailer", 3, 250, 200, 60, "E-flute, white top liner 140 GSM", "2-colour outside", "piece", 260000),
    ("L21", "mailer", 3, 300, 220, 80, "E-flute, white top liner 140 GSM", "2-colour outside", "piece", 180000),
    ("L22", "mailer", 3, 350, 250, 100, "E-flute, white top liner 140 GSM", "2-colour outside", "piece", 120000),
    ("L23", "partition", 3, 300, 250, 190, "6-cell (2x3) partition for L11, 3-ply", "Plain", "set", 120000),
    ("L24", "partition", 3, 400, 300, 290, "12-cell (3x4) partition for L13, 3-ply", "Plain", "set", 90000),
    ("L25", "pad", 3, 400, 300, 0, "3-ply layer pad, 120/100/120 GSM", "Plain", "piece", 200000),
    ("L26", "pad", 5, 600, 400, 0, "5-ply layer pad, 150/120/120/120/150 GSM", "Plain", "piece", 90000),
    ("L27", "roll", 2, 1200, 0, 0, "2-ply corrugated roll, 1200 mm width, 100/100 GSM", "Plain", "kg", 60000),
    ("L28", "roll", 2, 900, 0, 0, "2-ply corrugated roll, 900 mm width, 100/100 GSM", "Plain", "kg", 40000),
    ("L29", "rsc", 5, 800, 600, 500, "180/150/150/150/180 GSM, 24 BF, BC-flute, heavy duty", "Plain", "piece", 18000),
    ("L30", "rsc", 3, 320, 240, 120, "120/100/120 GSM, 16 BF, B-flute, tear-strip returns box", "2-colour", "piece", 160000),
]

RATE = {3: 38.0, 5: 42.0}


def weight_g(kind, ply, L, W, H, lid, spec=""):
    return est_weight_g(kind, ply, L, W, H, spec)


def describe(kind, ply, L, W, H, spec, prn):
    if kind == "rsc":
        return f"{ply}-ply RSC box {L} x {W} x {H} mm"
    if kind == "mailer":
        return f"Die-cut mailer box {L} x {W} x {H} mm, 3-ply E-flute"
    if kind == "partition":
        return spec.split(",")[0].replace(" for ", " set for ")
    if kind == "pad":
        return f"{ply}-ply layer pad {L} x {W} mm"
    return f"2-ply corrugated roll, {L} mm width"


LINES, BASE = [], {}
for lid, kind, ply, L, W, H, spec, prn, uom, qty in RAW:
    qty = int(round(qty * 0.57, -3))          # sized so the annual award is about Rs 4 crore
    w = weight_g(kind, ply, L, W, H, lid, spec)
    if kind == "roll":
        base = 36.5 if L == 1200 else 37.0
    else:
        factor = {"rsc": 1.0, "mailer": 1.45, "partition": 1.15, "pad": 0.98}[kind]
        if lid == "L30":
            factor = 1.18
        base = w / 1000 * RATE[5 if ply == 5 else 3] * factor
    BASE[lid] = base
    LINES.append({
        "id": lid, "category": {"rsc": f"{ply}-ply RSC", "mailer": "Die-cut mailer", "partition": "Partition",
                                "pad": "Layer pad", "roll": "Corrugated roll"}[kind],
        "kind": kind, "ply": ply, "description": describe(kind, ply, L, W, H, spec, prn),
        "length_mm": L or None, "width_mm": W or None, "height_mm": H or None,
        "board_spec": spec, "print": prn, "uom": uom, "annual_qty": qty,
        "est_weight_g": round(w) if w else None,
    })

# incumbent's last-year contract (per RFx uom, INR) — the "same as last year" reference
LAST_YEAR = {l["id"]: round(BASE[l["id"]] * random.uniform(1.015, 1.045), 2) for l in LINES}
for l in LINES:
    l["last_year_price"] = LAST_YEAR[l["id"]]

QUESTIONS = [
    {"id": "Q1", "text": "Do you hold a valid ISO 9001 certificate? Attach a copy.", "type": "yes_no",
     "mandatory": True, "rule": {"kind": "yes_and_valid"}, "pass_text": "Yes, and certificate valid on the award date"},
    {"id": "Q2", "text": "Do you have an in-house lab for bursting strength, ECT and Cobb testing?", "type": "yes_no",
     "mandatory": True, "rule": {"kind": "yes"}, "pass_text": "Yes"},
    {"id": "Q3", "text": "Customer rejection rate in the last financial year (% of pieces supplied)?", "type": "number",
     "mandatory": True, "rule": {"kind": "max", "value": 2.0}, "pass_text": "2.0% or lower"},
    {"id": "Q4", "text": "Monthly converting capacity (metric tonnes)?", "type": "number",
     "mandatory": True, "rule": {"kind": "min", "value": 300}, "pass_text": "300 MT or higher"},
    {"id": "Q5", "text": "Are you FSC chain-of-custody certified?", "type": "yes_no", "mandatory": False, "rule": None, "pass_text": "Information only"},
    {"id": "Q6", "text": "Standard lead time from PO to delivery at Bhiwandi (days)?", "type": "number", "mandatory": False, "rule": None, "pass_text": "Information only"},
    {"id": "Q7", "text": "Do you accept our standard payment terms of 60 days from GRN?", "type": "yes_no", "mandatory": False, "rule": None, "pass_text": "Information only"},
    {"id": "Q8", "text": "Share of recycled kraft in your liners (%)?", "type": "number", "mandatory": False, "rule": None, "pass_text": "Information only"},
]

VENDORS = [
    {"id": "V1", "name": "Shree Balaji Packaging Industries", "email": "sales@shreebalajipack.example", "city": "Vasai", "incumbent": False},
    {"id": "V2", "name": "Pacific Kraft Packaging (India) Pvt Ltd", "email": "quotes@pacifickraft.example", "city": "Vapi", "incumbent": False},
    {"id": "V3", "name": "Greenpack Solutions LLP", "email": "rohit@greenpacksolutions.example", "city": "Pune", "incumbent": False},
    {"id": "V4", "name": "Mahalaxmi Corrugators", "email": "mahalaxmi.corr@mailbox.example", "city": "Bhiwandi", "incumbent": False},
    {"id": "V5", "name": "Om Sai Packaging", "email": "omsaipack@mailbox.example", "city": "Bhiwandi", "incumbent": True},
]

RFX = {
    "id": "RFQ-2026-118",
    "title": "Annual rate contract: corrugated packaging, FY 2027-28",
    "buyer": {"company": "Saffron Basket Retail Pvt Ltd", "name": "Meera Iyer", "role": "Category Buyer, Packaging",
              "email": "meera.iyer@saffronbasket.example"},
    "scope": "Supply of corrugated shipper boxes, die-cut mailers, partitions, layer pads and corrugated rolls to the "
             "Saffron Basket fulfilment centre at Bhiwandi (Maharashtra) for 12 months from 1 April 2027, against "
             "monthly call-off schedules. Quantities are annual estimates, not commitments.",
    "currency": "INR",
    "due_date": "2026-11-10",
    "award_date": "2026-11-20",
    "terms": [
        "Prices to be quoted per RFx unit of measure, delivered Bhiwandi FC, exclusive of GST.",
        "Prices firm for 12 months; kraft paper index-linked revision only if proposed in the quote.",
        "Payment 60 days from GRN.",
        "Buyer may split the award across vendors line by line.",
        "Board specification as per each line; any deviation must be called out explicitly.",
    ],
    "lines": LINES,
    "questionnaire": QUESTIONS,
    "vendors": VENDORS,
    "last_year": {"vendor_id": "V5", "contract_ref": "RC-2025-071", "prices": LAST_YEAR},
    "fx": {"USD": FX_USD_INR},
}

# ---------------------------------------------------------------- true vendor prices (INR per RFx uom)
def vprice(lid, f3, f5, fother):
    l = next(x for x in LINES if x["id"] == lid)
    f = f3 if (l["kind"] == "rsc" and l["ply"] == 3) else f5 if (l["kind"] == "rsc" and l["ply"] == 5) else fother
    return BASE[lid] * f * random.uniform(0.975, 1.025)


TRUE = {
    "V1": {l["id"]: round(vprice(l["id"], 0.955, 1.045, 1.00), 2) for l in LINES},
    "V2": {l["id"]: vprice(l["id"], 1.03, 0.935, 0.97) for l in LINES},
    "V3": {l["id"]: round(vprice(l["id"], 0.99, 0.985, 0.93), 2) for l in LINES if l["id"] not in ("L27", "L28", "L29")},
    "V4": {l["id"]: round(vprice(l["id"], 0.975, 1.01, 1.04), 2) for l in LINES if l["id"] not in ("L27", "L28")},
}
# V2 quotes USD to 4 dp; truth is what that converts back to
V2_USD = {k: round(v / FX_USD_INR, 4) for k, v in TRUE["V2"].items()}
TRUE["V2"] = {k: round(v * FX_USD_INR, 4) for k, v in V2_USD.items()}
# V5: Rs 42/kg for 5-ply RSC, Rs 38/kg for 3-ply RSC, everything else at last year's price
TRUE["V5"] = {}
for l in LINES:
    if l["kind"] == "rsc":
        TRUE["V5"][l["id"]] = round((42 if l["ply"] == 5 else 38) * l["est_weight_g"] / 1000, 4)
    else:
        TRUE["V5"][l["id"]] = LAST_YEAR[l["id"]]


def inch(mmv):
    return f"{mmv / 25.4:.1f}".rstrip("0").rstrip(".")


def email(vid, subject, date, body, attachments):
    d = REPLIES / vid
    v = next(x for x in VENDORS if x["id"] == vid)
    (d / "email.json").write_text(json.dumps({
        "vendor_id": vid, "from_name": v["name"], "from": v["email"], "to": RFX["buyer"]["email"],
        "subject": subject, "date": date, "body": body, "attachments": attachments}, indent=2, ensure_ascii=False))


# ---------------------------------------------------------------- V1: Excel that ignores the template
def make_v1():
    d = REPLIES / "V1"; d.mkdir(parents=True, exist_ok=True)
    wb = Workbook()
    thin = Side(style="thin", color="999999")
    box = Border(left=thin, right=thin, top=thin, bottom=thin)
    hdr_fill = PatternFill("solid", fgColor="1F4E78")

    def sheet(ws, title, lines, start_sr):
        ws.merge_cells("A1:G1"); ws["A1"] = "SHREE BALAJI PACKAGING INDUSTRIES"
        ws["A1"].font = Font(bold=True, size=16, color="1F4E78"); ws["A1"].alignment = Alignment(horizontal="center")
        ws.merge_cells("A2:G2"); ws["A2"] = "Gala 14-17, Navghar Industrial Estate, Vasai (E) 401210  |  GSTIN 27AAKFS0000X1Z5"
        ws["A2"].alignment = Alignment(horizontal="center")
        ws.merge_cells("A4:G4"); ws["A4"] = f"OFFER NO. SBP/Q/2611/26  DT. 06.11.2026   —   {title}"
        ws["A4"].font = Font(bold=True)
        ws["A5"] = "Kind Attn: Ms. Meera Iyer, Saffron Basket Retail Pvt Ltd  (Your enquiry RFQ-2026-118)"
        heads = ["Sr", "Our Item Code", "Box Size L x W x H (inch)", "Board / Ply", "Qty per annum (Nos)", "Rate / 100 Nos (Rs.)", "Annual Value (Rs.)"]
        for c, h in enumerate(heads, 1):
            cell = ws.cell(row=7, column=c, value=h)
            cell.font = Font(bold=True, color="FFFFFF"); cell.fill = hdr_fill; cell.border = box
            cell.alignment = Alignment(wrap_text=True, horizontal="center", vertical="center")
        r, total = 8, 0.0
        for i, l in enumerate(lines):
            p = TRUE["V1"][l["id"]]
            if l["kind"] == "rsc":
                size = f'{inch(l["length_mm"])} x {inch(l["width_mm"])} x {inch(l["height_mm"])}"'
                board = f'{l["ply"]} PLY RSC' + (" H.D." if l["id"] == "L29" else "") + (" RETURN BOX 2 COL" if l["id"] == "L30" else "")
            elif l["kind"] == "mailer":
                size = f'{inch(l["length_mm"])} x {inch(l["width_mm"])} x {inch(l["height_mm"])}"'
                board = "E FLUTE DIE CUT MAILER 2 COL PRTG"
            elif l["kind"] == "partition":
                size = "6 CELL PARTITION (2x3)" if l["id"] == "L23" else "12 CELL PARTITION (3x4)"
                board = "3 PLY, PER SET"
            elif l["kind"] == "pad":
                size = f'{inch(l["length_mm"])} x {inch(l["width_mm"])}" PAD'
                board = f'{l["ply"]} PLY SHEET'
            else:
                size = f'CORR. ROLL {inch(l["length_mm"])}" WIDTH'
                board = "2 PLY ROLL — RATE PER KG"
            code = f'SBP-{"R" if l["kind"]=="rsc" else "X"}{l["ply"]}-{random.randint(1000, 9999)}'
            amount = round(p * l["annual_qty"])
            if l["kind"] == "roll":
                rate = p                       # rolls are quoted per kg, stated in the description
            elif l["id"] == "L19":
                rate = p                       # the typo: per-piece rate typed into the per-100 column
            else:
                rate = round(p * 100, 2)
            vals = [start_sr + i, code, size, board, l["annual_qty"], rate, amount]
            for c, v in enumerate(vals, 1):
                cell = ws.cell(row=r, column=c, value=v); cell.border = box
                if c >= 5:
                    cell.number_format = "#,##0.00" if c == 6 else "#,##0"
            total += amount; r += 1
        ws.cell(row=r, column=6, value="SHEET TOTAL").font = Font(bold=True)
        ws.cell(row=r, column=7, value=round(total)).number_format = "#,##0"
        for col, wdt in zip("ABCDEFG", (6, 18, 30, 34, 16, 16, 18)):
            ws.column_dimensions[col].width = wdt
        return r

    three = [l for l in LINES if l["kind"] == "rsc" and l["ply"] == 3]
    rest = [l for l in LINES if not (l["kind"] == "rsc" and l["ply"] == 3)]
    random.shuffle(rest)   # their own order, not ours
    ws1 = wb.active; ws1.title = "3 PLY"
    sheet(ws1, "3 PLY BOXES", three, 1)
    ws2 = wb.create_sheet("5 PLY & OTHERS")
    r = sheet(ws2, "5 PLY BOXES, MAILERS, PARTITIONS, PADS, ROLLS", rest, len(three) + 1)
    notes = ["Terms:", "1. Above rates are per 100 Nos unless mentioned, delivered at your Bhiwandi godown.",
             "2. GST extra as applicable.", "3. Payment: 45 days from date of invoice.",
             "4. Rates valid upto 31.01.2027. Paper rate variation beyond +/- 5% to be mutually discussed.",
             "5. Delivery: 7 days from schedule."]
    for i, n in enumerate(notes):
        ws2.cell(row=r + 2 + i, column=1, value=n)
    ws3 = wb.create_sheet("PROFILE")
    prof = [("COMPANY PROFILE / VENDOR QUESTIONNAIRE", ""), ("Year of establishment", "1998"),
            ("ISO 9001:2015", "Yes — Cert. No. QMS/IN/48213, valid till 22.06.2028 (copy can be shared on request)"),
            ("Testing facility", "In house lab: BS tester, ECT, Cobb, GSM, moisture meter"),
            ("Rejection % (FY 25-26)", "0.9%"), ("Installed capacity", "650 MT / month (5 ply automatic plant)"),
            ("FSC", "Not certified. Application in process."), ("Delivery lead time", "7 days"),
            ("Credit terms", "45 days. 60 days not acceptable at quoted rates."), ("Recycled kraft content", "Approx 80%")]
    for i, (a, b) in enumerate(prof, 1):
        ws3.cell(row=i, column=1, value=a).font = Font(bold=True)
        ws3.cell(row=i, column=2, value=b)
    ws3.column_dimensions["A"].width = 32; ws3.column_dimensions["B"].width = 80
    wb.save(d / "SBP Offer Saffron Basket 06-11-26.xlsx")

    # attached doc: bursting strength test report
    doc = SimpleDocTemplate(str(d / "BS Test Report Oct-26.pdf"), pagesize=A4, topMargin=18 * mm)
    st = getSampleStyleSheet()
    el = [Paragraph("<b>SHREE BALAJI PACKAGING INDUSTRIES — QC LABORATORY</b>", st["Title"]),
          Paragraph("Test Report No. QC/BS/1026/114 &nbsp;&nbsp; Date: 28-10-2026", st["Normal"]), Spacer(1, 8),
          Paragraph("Sample: 5 ply BC flute board 150/120/120/120/150 GSM, 3 ply B flute board 120/100/120 GSM. "
                    "Conditioning 27±2 °C, 65±5% RH. Method: IS 1060 (Part 1).", st["Normal"]), Spacer(1, 10)]
    t = Table([["Parameter", "Spec", "3 ply result", "5 ply result"],
               ["Bursting strength (kg/cm²)", "min 6.0 / 11.0", "6.8", "12.4"],
               ["Edge crush (kN/m)", "min 3.5 / 7.0", "4.1", "7.9"],
               ["Cobb 60 (g/m²)", "max 155", "138", "141"],
               ["Moisture (%)", "7 – 9", "8.1", "8.3"]], colWidths=[60 * mm, 35 * mm, 35 * mm, 35 * mm])
    t.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.5, colors.grey), ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey)]))
    el += [t, Spacer(1, 12), Paragraph("Result: Conforms. Tested by: S. Naik (QC In-charge)", st["Normal"])]
    doc.build(el)
    email("V1", "RE: RFQ-2026-118 Corrugated packaging FY27-28 — our offer", "2026-11-06T17:42:00+05:30",
          "Dear Madam,\n\nPlease find attached our best offer against your above enquiry along with our lab test report.\n"
          "We have quoted for all items. Kindly note rates are as per our standard format.\n\n"
          "Regards,\nJignesh Shah\nShree Balaji Packaging Industries\n+91 98200 00000",
          ["SBP Offer Saffron Basket 06-11-26.xlsx", "BS Test Report Oct-26.pdf"])


# ---------------------------------------------------------------- V2: PDF on letterhead, USD, discount in a footnote
def make_v2():
    d = REPLIES / "V2"; d.mkdir(parents=True, exist_ok=True)
    st = getSampleStyleSheet()
    small = ParagraphStyle("small", parent=st["Normal"], fontSize=6.5, leading=8, textColor=colors.HexColor("#555555"))
    body = ParagraphStyle("body", parent=st["Normal"], fontSize=9, leading=12)

    def letterhead(canvas, doc):
        canvas.saveState()
        canvas.setFillColor(colors.HexColor("#0B3D5C")); canvas.rect(0, A4[1] - 26 * mm, A4[0], 26 * mm, fill=1, stroke=0)
        canvas.setFillColor(colors.white); canvas.setFont("Helvetica-Bold", 17)
        canvas.drawString(18 * mm, A4[1] - 13 * mm, "PACIFIC KRAFT PACKAGING (INDIA) PVT LTD")
        canvas.setFont("Helvetica", 8)
        canvas.drawString(18 * mm, A4[1] - 19 * mm, "A subsidiary of Pacific Kraft Holdings Pte Ltd, Singapore  •  Plot 212, GIDC Phase III, Vapi 396195, Gujarat")
        canvas.setFillColor(colors.grey); canvas.setFont("Helvetica", 7)
        canvas.drawString(18 * mm, 10 * mm, "CIN U21010GJ2011FTC000000  •  www.pacifickraft.example  •  Page %d" % doc.page)
        canvas.restoreState()

    doc = SimpleDocTemplate(str(d / "PKP_Quotation_Q-26-4471.pdf"), pagesize=A4, topMargin=32 * mm, bottomMargin=18 * mm,
                            leftMargin=18 * mm, rightMargin=18 * mm)
    el = [Paragraph("<b>QUOTATION Q-26-4471</b> &nbsp;&nbsp;&nbsp; Date: 7 November 2026", body),
          Paragraph("To: Saffron Basket Retail Pvt Ltd, Attn. Ms Meera Iyer &nbsp;&nbsp; Ref: RFQ-2026-118", body), Spacer(1, 6),
          Paragraph("We thank you for the enquiry and are pleased to offer as below. As per group policy all commercial "
                    "offers are denominated in US Dollars.", body), Spacer(1, 6)]
    rows = [["#", "Description", "Board", "UoM", "Annual qty", "Unit price (USD)¹", "Extended (USD)"]]
    tot = 0
    for i, l in enumerate(LINES, 1):
        usd = V2_USD[l["id"]]
        ext = usd * l["annual_qty"]; tot += ext
        desc = l["description"].replace(" mm", "").replace(" x ", "×")
        rows.append([str(i), desc, l["board_spec"].split(",")[0][:28], {"piece": "EA", "set": "SET", "kg": "KG"}[l["uom"]],
                     f'{l["annual_qty"]:,}', f"{usd:.4f}", f"{ext:,.0f}"])
    rows.append(["", "", "", "", "", "Total", f"{tot:,.0f}"])
    t = Table(rows, colWidths=[7 * mm, 62 * mm, 40 * mm, 11 * mm, 19 * mm, 18 * mm, 19 * mm], repeatRows=1)
    t.setStyle(TableStyle([("FONTSIZE", (0, 0), (-1, -1), 6.6), ("GRID", (0, 0), (-1, -2), 0.3, colors.HexColor("#AAAAAA")),
                           ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#DCE6EC")), ("ALIGN", (4, 0), (-1, -1), "RIGHT"),
                           ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"), ("FONTNAME", (5, -1), (-1, -1), "Helvetica-Bold"),
                           ("TOPPADDING", (0, 0), (-1, -1), 1.6), ("BOTTOMPADDING", (0, 0), (-1, -1), 1.6)]))
    el += [t, Spacer(1, 5),
           Paragraph("¹ Unit prices are list prices. A volume rebate of 4.0% on all lines applies where the awarded annual "
                     "value to Pacific Kraft exceeds USD 250,000; below this threshold list prices apply. Prices are delivered "
                     "Bhiwandi (DAP), exclusive of GST. Invoicing in INR at the RBI reference rate on invoice date. "
                     "Validity 60 days from quotation date. Payment 60 days from GRN accepted.", small),
           PageBreak(),
           Paragraph("<b>Annexure A — Supplier questionnaire responses</b>", body), Spacer(1, 6)]
    q = [["Question", "Response"],
         ["ISO 9001 certification", "Yes. ISO 9001:2015, certificate attached (valid to 14 March 2028)."],
         ["In-house test laboratory", "Yes — BS, ECT, Cobb, compression (BCT) and drop test."],
         ["Customer rejection rate, last FY", "0.6% of pieces supplied"],
         ["Monthly converting capacity", "1,400 MT"],
         ["FSC chain of custody", "Yes, FSC-C000000"],
         ["Lead time to Bhiwandi", "10 days from PO; 5 days against rolling forecast"],
         ["60-day payment terms", "Accepted"],
         ["Recycled kraft share in liners", "65%"]]
    qt = Table(q, colWidths=[60 * mm, 110 * mm])
    qt.setStyle(TableStyle([("FONTSIZE", (0, 0), (-1, -1), 8.5), ("GRID", (0, 0), (-1, -1), 0.3, colors.grey),
                            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#DCE6EC"))]))
    el += [qt, Spacer(1, 14), Paragraph("For Pacific Kraft Packaging (India) Pvt Ltd<br/><br/>Anita Deshmukh<br/>Key Account Manager", body)]
    doc.build(el, onFirstPage=letterhead, onLaterPages=letterhead)

    cert = SimpleDocTemplate(str(d / "ISO9001_Certificate_PKP.pdf"), pagesize=A4, topMargin=40 * mm)
    c = [Paragraph("CERTIFICATE OF REGISTRATION", st["Title"]), Spacer(1, 12),
         Paragraph("This is to certify that the Quality Management System of", st["Normal"]), Spacer(1, 8),
         Paragraph("<b>Pacific Kraft Packaging (India) Pvt Ltd</b><br/>Plot 212, GIDC Phase III, Vapi 396195, Gujarat, India", st["Heading3"]),
         Spacer(1, 8), Paragraph("has been assessed and found to conform to <b>ISO 9001:2015</b> for the scope: "
                                  "Manufacture and supply of corrugated boards, boxes and allied packaging.", st["Normal"]),
         Spacer(1, 14), Paragraph("Certificate No: IN-QMS-771204<br/>Date of initial registration: 15 March 2019<br/>"
                                  "Date of issue: 15 March 2025<br/><b>Valid until: 14 March 2028</b>", st["Normal"]),
         Spacer(1, 20), Paragraph("Issued by Veritas Assurance Services (fictional certification body)", st["Italic"])]
    cert.build(c)
    email("V2", "Pacific Kraft quotation Q-26-4471 against RFQ-2026-118", "2026-11-07T11:05:00+05:30",
          "Dear Meera,\n\nAttached is our quotation for the FY27-28 corrugated requirement together with our ISO certificate. "
          "Questionnaire responses are in Annexure A of the quotation.\n\nHappy to walk you through it on a call.\n\n"
          "Best regards,\nAnita Deshmukh\nKey Account Manager, Pacific Kraft Packaging (India)",
          ["PKP_Quotation_Q-26-4471.pdf", "ISO9001_Certificate_PKP.pdf"])


# ---------------------------------------------------------------- V3: Word doc, commercials in paragraphs, 27 of 30
def make_v3():
    d = REPLIES / "V3"; d.mkdir(parents=True, exist_ok=True)
    P = TRUE["V3"]
    doc = Document()
    doc.styles["Normal"].font.size = Pt(10.5)
    doc.add_heading("Greenpack Solutions LLP", 1)
    doc.add_paragraph("Gat No. 418, Chakan-Talegaon Road, Pune 410501\n8 November 2026")
    doc.add_paragraph("To,\nMs. Meera Iyer\nCategory Buyer – Packaging\nSaffron Basket Retail Pvt Ltd")
    doc.add_paragraph("Subject: Our proposal against RFQ-2026-118 (corrugated packaging, FY 2027-28)")
    doc.add_paragraph("Dear Ms. Iyer,")
    doc.add_paragraph(
        "Thank you for inviting Greenpack to participate. We have studied the thirty items and are glad to submit our "
        "proposal for twenty-seven of them. We regret that we do not manufacture 2-ply corrugated rolls in either width, "
        "and the heavy-duty 800 x 600 x 500 master carton is beyond the deckle of our corrugator, so we are not "
        "quoting for those three items.")
    doc.add_heading("Commercial offer", 2)
    def seq(ids):
        out = []
        for lid in ids:
            l = next(x for x in LINES if x["id"] == lid)
            out.append(f'{l["length_mm"]}x{l["width_mm"]}x{l["height_mm"]} at Rs. {P[lid]:.2f}')
        return ", ".join(out[:-1]) + " and " + out[-1]
    doc.add_paragraph(
        "For the 3-ply regular slotted boxes, our per-box prices delivered to Bhiwandi are as follows: "
        + seq(["L01", "L02", "L03", "L04", "L05", "L06", "L07", "L08", "L09", "L10"]) + ". "
        f'The 2-colour tear-strip returns box (320x240x120) will be Rs. {P["L30"]:.2f} per box.')
    doc.add_paragraph(
        "For the 5-ply boxes the prices per box are " + seq(["L11", "L12", "L13", "L14", "L15", "L16", "L17"]) + ". "
        f'For the largest 5-ply size, 750x500x450, we propose 140 GSM outer liners in place of the specified 150 GSM, which '
        f'in our experience meets the same 22 BF requirement, and on that basis our price is Rs. {P["L18"]:.2f} per box.')
    doc.add_paragraph(
        "Die-cut mailers are our speciality. Per thousand mailers, with 2-colour printing, we offer "
        f'Rs. {P["L19"]*1000:,.0f} for 200x150x50, Rs. {P["L20"]*1000:,.0f} for 250x200x60, Rs. {P["L21"]*1000:,.0f} for 300x220x80 '
        f'and Rs. {P["L22"]*1000:,.0f} for 350x250x100. A one-time die and block charge of Rs. 18,000 per size is payable with the first order.')
    doc.add_paragraph(
        f'Partitions will be supplied at Rs. {P["L23"]:.2f} per set for the 6-cell and Rs. {P["L24"]:.2f} per set for the 12-cell. '
        f'Layer pads are Rs. {P["L25"]:.2f} each for the 3-ply 400x300 and Rs. {P["L26"]:.2f} each for the 5-ply 600x400.')
    doc.add_paragraph(
        "All prices are in Indian Rupees, exclusive of GST, and include delivery to your Bhiwandi fulfilment centre in "
        "full truck loads; part loads below 4 MT will attract freight at actuals. Prices are firm until 31 March 2027 and "
        "thereafter linked to the kraft paper index quarterly. We request payment within 30 days of invoice.")
    doc.add_heading("About our quality systems", 2)
    doc.add_paragraph(
        "Greenpack has been ISO 9001 certified since 2017 and a copy of our certificate is attached. We run a fully "
        "equipped in-house laboratory covering bursting strength, edge crush and Cobb. Our monthly converting capacity "
        "is around 420 tonnes. In the last financial year our customer rejection rate was 2.8%, mainly on account of a "
        "print registration issue on one account which has since been resolved. We are FSC certified and use about 90% "
        "recycled kraft. Our normal lead time to Bhiwandi is 12 days from receipt of the purchase order.")
    doc.add_paragraph("We look forward to a long association.\n\nYours sincerely,\nRohit Kulkarni\nPartner, Greenpack Solutions LLP")
    doc.save(d / "Greenpack Proposal - Saffron Basket.docx")

    st = getSampleStyleSheet()
    cert = SimpleDocTemplate(str(d / "Greenpack ISO 9001.pdf"), pagesize=A4, topMargin=40 * mm)
    cert.build([Paragraph("CERTIFICATE", st["Title"]), Spacer(1, 12),
                Paragraph("Quality Management System — <b>ISO 9001:2015</b>", st["Heading3"]), Spacer(1, 8),
                Paragraph("<b>Greenpack Solutions LLP</b>, Gat No. 418, Chakan-Talegaon Road, Pune 410501", st["Normal"]),
                Spacer(1, 8), Paragraph("Scope: Manufacture of corrugated boxes and die-cut cartons.", st["Normal"]), Spacer(1, 14),
                Paragraph("Certificate No: QM-17-2290<br/>Issue date: 1 September 2023<br/><b>Expiry date: 31 August 2026</b>", st["Normal"]),
                Spacer(1, 20), Paragraph("Issued by Sahyadri Certifications (fictional certification body)", st["Italic"])])
    email("V3", "Greenpack proposal — RFQ-2026-118", "2026-11-08T09:20:00+05:30",
          "Dear Ms. Iyer,\n\nPlease see our proposal letter attached, along with our ISO certificate.\n\nWarm regards,\nRohit Kulkarni",
          ["Greenpack Proposal - Saffron Basket.docx", "Greenpack ISO 9001.pdf"])


# ---------------------------------------------------------------- V4: phone photo of a printed rate card
def make_v4():
    d = REPLIES / "V4"; d.mkdir(parents=True, exist_ok=True)
    P = TRUE["V4"]
    W, H = 1500, 2050
    card = Image.new("RGB", (W, H), (250, 248, 240))
    dr = ImageDraw.Draw(card)
    F = "/usr/share/fonts/truetype/dejavu/DejaVuSans%s.ttf"
    f_h = ImageFont.truetype(F % "-Bold", 46); f_b = ImageFont.truetype(F % "-Bold", 25)
    f_n = ImageFont.truetype(F % "", 25); f_s = ImageFont.truetype(F % "", 21)
    dr.rectangle([20, 20, W - 20, H - 20], outline=(120, 20, 20), width=5)
    dr.text((W // 2, 75), "MAHALAXMI CORRUGATORS", font=f_h, fill=(120, 20, 20), anchor="mm")
    dr.text((W // 2, 125), "Mfrs. of Corrugated Boxes, Rolls & Sheets  •  Kalher, Bhiwandi 421302  •  Mob. 93200 00000", font=f_s, fill=(40, 40, 40), anchor="mm")
    dr.text((W // 2, 170), "RATE CARD for M/s SAFFRON BASKET  —  Ref RFQ-2026-118  —  w.e.f. 01.04.2027", font=f_b, fill=(0, 0, 0), anchor="mm")
    cols = [60, 140, 640, 900, 1130, 1440]
    y = 215
    def row(vals, font, yy, fill=None):
        if fill:
            dr.rectangle([cols[0], yy, cols[-1], yy + 50], fill=fill)
        for i, v in enumerate(vals):
            dr.text((cols[i] + 12, yy + 11), str(v), font=font, fill=(0, 0, 0))
        dr.line([cols[0], yy + 50, cols[-1], yy + 50], fill=(90, 90, 90), width=2)
    row(["No", "Item / Size (mm)", "Board", "Unit", "Rate (Rs.)"], f_b, y, (225, 215, 195)); top = y; y += 50
    n = 0
    for l in LINES:
        if l["id"] not in P:
            continue
        n += 1
        if l["kind"] == "rsc":
            item = f'{l["length_mm"]} x {l["width_mm"]} x {l["height_mm"]}' + ("  HD" if l["id"] == "L29" else "  Return box" if l["id"] == "L30" else "")
            board, unit, rate = f'{l["ply"]} Ply', "per box", f'{P[l["id"]]:.2f}'
        elif l["kind"] == "mailer":
            item = f'Mailer {l["length_mm"]} x {l["width_mm"]} x {l["height_mm"]}'
            board, unit, rate = "E flute 2 col", "per 100", f'{P[l["id"]]*100:.0f}'
        elif l["kind"] == "partition":
            item = "Partition 6 cell" if l["id"] == "L23" else "Partition 12 cell"
            board, unit, rate = "3 Ply", "per set", f'{P[l["id"]]:.2f}'
        else:
            item = f'Pad {l["length_mm"]} x {l["width_mm"]}'
            board, unit, rate = f'{l["ply"]} Ply', "per 100", f'{P[l["id"]]*100:.0f}'
        row([n, item, board, unit, rate], f_n, y); y += 50
    for x in cols:
        dr.line([x, top, x, y], fill=(90, 90, 90), width=2)
    dr.line([cols[0], top, cols[-1], top], fill=(90, 90, 90), width=2)
    y += 22
    for t in ["Rates delivered Bhiwandi. GST extra. Payment 30 days.", "Corrugated rolls not manufactured by us.",
              "Rates subject to paper market. Valid for orders booked before 31.12.2026."]:
        dr.text((70, y), "•  " + t, font=f_s, fill=(30, 30, 30)); y += 34
    # handwritten-style note in blue ink + stamp
    f_i = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSerif-Bold.ttf", 30)
    note = Image.new("RGBA", (760, 110), (0, 0, 0, 0))
    ImageDraw.Draw(note).text((5, 5), "Less 2% if full qty given to us\n         - Prakash", font=f_i, fill=(25, 45, 150, 235))
    note = note.rotate(4, expand=True, resample=Image.BICUBIC)
    card.paste(note, (700, y - 50), note)
    stamp = Image.new("RGBA", (300, 300), (0, 0, 0, 0)); sd = ImageDraw.Draw(stamp)
    sd.ellipse([10, 10, 290, 290], outline=(90, 40, 140, 170), width=7)
    sd.text((150, 130), "MAHALAXMI", font=f_b, fill=(90, 40, 140, 170), anchor="mm")
    sd.text((150, 170), "CORRUGATORS", font=f_s, fill=(90, 40, 140, 170), anchor="mm")
    stamp = stamp.rotate(-14, expand=True, resample=Image.BICUBIC)
    card.paste(stamp, (1120, 1395), stamp)     # stamped across the last few rates, as happens

    # put it on a desk, shot at an angle
    PW, PH = 2100, 2800
    rng = np.random.default_rng(3)
    desk = np.zeros((PH, PW, 3), dtype=np.float32)
    desk[:] = (98, 70, 48)
    desk += rng.normal(0, 7, (PH, 1, 1)).astype(np.float32)        # wood grain
    desk += rng.normal(0, 3, (PH, PW, 1)).astype(np.float32)
    photo = Image.fromarray(np.clip(desk, 0, 255).astype(np.uint8))
    dst = [(330, 250), (1790, 130), (2010, 2560), (120, 2690)]     # TL, TR, BR, BL on the photo
    src = [(0, 0), (W, 0), (W, H), (0, H)]
    A = []
    for (x, y_), (u, v) in zip(dst, src):
        A.append([x, y_, 1, 0, 0, 0, -u * x, -u * y_]); A.append([0, 0, 0, x, y_, 1, -v * x, -v * y_])
    coeffs = np.linalg.solve(np.array(A, dtype=float), np.array([c for p in src for c in p], dtype=float))
    warped = card.convert("RGBA").transform((PW, PH), Image.PERSPECTIVE, tuple(coeffs), Image.BICUBIC)
    shadow = Image.new("RGBA", (PW, PH), (0, 0, 0, 0))
    ImageDraw.Draw(shadow).polygon([(x + 22, y_ + 26) for x, y_ in dst], fill=(0, 0, 0, 110))
    photo.paste(shadow.filter(ImageFilter.GaussianBlur(18)), (0, 0), shadow.filter(ImageFilter.GaussianBlur(18)))
    photo.paste(warped, (0, 0), warped)
    arr = np.asarray(photo).astype(np.float32)
    yy, xx = np.mgrid[0:PH, 0:PW]
    light = 1.08 - 0.33 * ((xx / PW) * 0.55 + (yy / PH) * 0.45)     # light falls off towards bottom-right
    glare = 38 * np.exp(-(((xx - 620) / 380) ** 2 + ((yy - 760) / 300) ** 2))
    arr = arr * light[..., None] + glare[..., None] + rng.normal(0, 4.5, arr.shape)
    photo = Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8)).filter(ImageFilter.GaussianBlur(1.1))
    photo = photo.rotate(-2.2, resample=Image.BICUBIC, fillcolor=(80, 58, 40))
    photo.save(d / "IMG_20261109_184512.jpg", quality=72)
    email("V4", "rate", "2026-11-09T18:47:00+05:30",
          "Madam rate card photo attached pls check. Sent from my phone", ["IMG_20261109_184512.jpg"])


# ---------------------------------------------------------------- V5: the one-line email
def make_v5():
    (REPLIES / "V5").mkdir(parents=True, exist_ok=True)
    email("V5", "Re: RFQ-2026-118", "2026-11-10T21:14:00+05:30",
          "Meera ji,\n\n₹42/kg for the 5-ply, 38 for the 3-ply, rest same as last year, freight extra.\n\nSuresh\nOm Sai Packaging", [])


if __name__ == "__main__":
    SEED.mkdir(parents=True, exist_ok=True)
    (SEED / "rfx.json").write_text(json.dumps(RFX, indent=2, ensure_ascii=False))
    (SEED / "truth.json").write_text(json.dumps(TRUE, indent=2))
    for f in (make_v1, make_v2, make_v3, make_v4, make_v5):
        f()
    tot = {v: sum(p * next(l for l in LINES if l["id"] == k)["annual_qty"] for k, p in ps.items()) for v, ps in TRUE.items()}
    print("annual value by vendor (Rs. lakh):", {k: round(v / 1e5, 1) for k, v in tot.items()})
    print("lines quoted:", {k: len(v) for k, v in TRUE.items()})
