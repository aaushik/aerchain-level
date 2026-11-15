# Demo and recording script

About 9 minutes. Start the server, open http://localhost:8000, click Reset.

## 1. Draft (1 min)
Use the first two starter prompts under the chat. Point at the draft filling in on the right: 30 lines imported from last year's contract, questionnaire with pass rules. Say: the co-pilot's job is to pin down units and pass criteria so the comparison is possible later. Send to vendors.

If the model is slow, click "load the finished demo RFx" instead.

## 2. Responses (1 min)
"Receive vendor replies". Open the attachments so the reviewer sees the mess: the Excel in its own format, the USD PDF, the Word letter, the photo, the one-line email. "Read responses" (about 2 minutes live; or use "Open the demo with all five responses already read" from a fresh Reset).

## 3. Compare (3 min): the part that matters
The grid is colour only, and the legend above it names each colour: white "Clear", yellow "Check assumption", red "Likely wrong", hatched "Not quoted", a thin amber left edge "Photo", and an underline for the lowest price that can win. Hover a cell for its one-line reason. Click a cell for the verdict, the vendor's own text and the actions; everything else is under "Details". Click a vendor card for its open issues and terms.

| Click | What it shows |
|---|---|
| Shree Balaji, L19 (Likely wrong, ₹0.04) | One sentence: the vendor's own total implies ₹4.00, and the price is 99% below the others. The vendor's spreadsheet row is shown as a small table under its own column headings, with the rate cell highlighted. "Use ₹4.00 instead" is the main button; keeping ₹0.04 needs a typed reason. |
| Pacific Kraft card | "Every price: converted from USD" is one open issue on the vendor, not 30 amber cells. Change the USD rate at top right and watch the column move. |
| Om Sai, L11 (Check assumption) | Per-kg rate times a calculated weight the vendor has not confirmed. "Accept for all 18 Om Sai lines with this assumption" resolves the group in one step, with one entry in the decision log and one Undo in the vendor panel. To approve only some, click "Choose lines…" (or "Choose lines" under the issue in the vendor panel), untick the ones you doubt, and "Accept 7 of 18 lines"; the rest stay open. |
| Om Sai, L19 (Likely wrong) | Does "38 for the 3-ply" include 3-ply mailers? The system does not guess. |
| Mahalaxmi, any line (Photo edge) | Read from the photo; the photo is the proof. |
| Greenpack, L18 (Likely wrong) | 140 GSM offered where 150 was specified. |

Then open the Greenpack card: quality failed (ISO certificate attached, but expired in August). Open the Pacific Kraft card and tick the conditional 4% discount on and off.

Open the Om Sai card: freight is extra with no amount, so its prices are ex-works against everyone else's delivered prices and are held back from awards (none can carry the underline).

## 3b. Close the loop with Om Sai (1.5 min)
1. In the Om Sai panel, "Ask vendor (draft clarification)". The questions come from its open issues: which lines the 38/kg covers, freight, last year's prices, the four unanswered mandatory questions.
2. "Record vendor reply", then "Paste the sample reply from Om Sai Packaging" (Suresh's answer: kg rates are for RSC boxes only, mailers, partitions, pads and rolls restated at last year's prices, freight 2.5%, questionnaire answered, ISO certificate "I will send separately"). "Record reply and re-level" makes one live model call over the whole thread (about a minute).
3. Om Sai's Likely wrong cells turn into prices with a ↻ marker; the panel reads "Re-levelled after the vendor's reply: likely wrong 10 → 0, freight now stated, quality still incomplete". Open L19: Details shows the price before the reply, and the proof is the reply. The decision log records the reply and the re-level.
4. Say the point: the freight hold-back lifts because freight is now stated, but Om Sai is not cleared, because it claims ISO 9001 and sends no copy. The product does not clear a vendor on a claim. "Ask vendor" again: the next question asks for the certificate.

## 4. Ask (3 min)
1. *What if we split it, cheapest per line, but only among vendors who cleared the quality questionnaire?* (the VP's question from the brief). Only Pacific Kraft has cleared: Shree Balaji and Om Sai state ISO validity without attaching a certificate, so they are incomplete. The scenario card's "if one excluded vendor were added back" line shows what each certificate is worth chasing for. If you ask before the Om Sai reply, the scenario card lists Om Sai's prices as held back for freight.
2. *What is that quality gate costing us versus a split across everyone? Who would be worth chasing?*
3. *Export the award recommendation to Excel.*

These are the three starter questions. Each answer is a sentence or two, one card or table, and one "Show working" disclosure (checks, line-by-line allocation, queries, model). A tick on "Show working" means every figure and vendor checked out; a failed check shows above it in plain words; a fallback model shows as a small marker. Off-script ideas: two-vendor award, comparison with last year as a chart.

## If they drive
Good things for a reviewer to try: upload their own file on the Responses screen ("Add a response by hand"); ask the analyst something off-script; change the FX rate and re-ask.
