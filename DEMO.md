# Demo and recording script

About 8 minutes. Start the server, open http://localhost:8000, click Reset.

## 1. Draft (1 min)
Use the first two starter prompts under the chat. Point at the draft filling in on the right: 30 lines imported from last year's contract, questionnaire with pass rules. Say: the co-pilot's job is to pin down units and pass criteria so the comparison is possible later. Send to vendors.

If the model is slow, click "load the finished demo RFx" instead.

## 2. Responses (1 min)
"Receive vendor replies". Open the attachments so the reviewer sees the mess: the Excel in its own format, the USD PDF, the Word letter, the photo, the one-line email. "Read responses" (about 2 minutes live; or use "Open the demo with all five responses already read" from a fresh Reset).

## 3. Compare (3 min): the part that matters
Click these cells in order:

| Cell | What it shows |
|---|---|
| Shree Balaji, L19 (red, ₹0.04) | Rate typed per piece in a per-100 column. Caught by the vendor's own line total and by the outlier check. Click "Use ₹4.00 instead". |
| Pacific Kraft, any line (amber, USD) | Currency conversion as a stated assumption. Change the USD rate at top right and watch the column move. |
| Om Sai, L11 (amber, per kg) | ₹42/kg times a calculated box weight. The weight is the assumption. |
| Om Sai, L19 (red, covered?) | Does "38 for the 3-ply" include 3-ply mailers? The system does not guess. |
| Om Sai, L27 (amber, last year) | "Rest same as last year" resolved from the prior contract, flagged for restating. |
| Mahalaxmi, any line (amber, photo) | Read from the photo, image shown beside the reading. |
| Greenpack, L18 (red, spec differs) | 140 GSM offered where 150 was specified. |

Then: Greenpack's "Quality: failed" badge (ISO certificate attached, but expired in August; rejection rate 2.8%). "Draft clarification email" for Om Sai. Tick Pacific Kraft's conditional 4% discount on and off.

## 4. Ask (3 min)
1. *What if we split it, cheapest per line, but only among vendors who cleared the quality questionnaire?* (the VP's question from the brief)
2. *What is that quality gate costing us versus a split across everyone? Who would be worth chasing?*
3. *If I can only manage two vendors, which two, and what do I give up?*
4. *Compare each vendor to last year's prices on the lines all five quoted. Chart it.*
5. *Export the award recommendation to Excel.*

On one answer, open "How this was computed" and point at the figure check under the answer.

## If they drive
Good things for a reviewer to try: upload their own file on the Responses screen ("Add a response by hand"); ask the analyst something off-script; change the FX rate and re-ask.
