# Level: what I decided, and what I left out

**The bet.** Extraction is not the hard part any more; a current model reads an angled photo of a rate card correctly. The hard part is that a buyer cannot tell which of 150 numbers to distrust. So the product is built around one question: *for every number on screen, can the buyer see where it came from and why it might be wrong?*

## Decisions

**1. The model reads. Code computes.** The model's only job in extraction is to copy what the vendor wrote: amount, currency, "per how many of what", which RFx line, and a verbatim snippet as evidence. It is told never to convert anything. Unit conversion, FX, discounts, totals and questionnaire pass/fail are deterministic code. The same rule holds in the analyst: the model writes SQL or calls an exact award engine, and never does arithmetic. A wrong sum on a ₹4 crore award is not a recoverable error.

**2. No confidence percentages.** A model's "92% confident" means nothing a buyer can act on. Each cell instead has a status derived from named, checkable reasons:
- *Verified*: unit and currency match the RFx, the cited text was found in the source, price is in line with other vendors.
- *Assumed*: usable, but rests on something stated (an FX rate, a calculated box weight for a per-kg quote, last year's price for "same as last year", a photo that cannot be text-searched).
- *Needs review*: probably wrong or not like-for-like (vendor's own line total contradicts the rate, price is a 2x outlier, spec substitution, unclear whether a group rate covers the line).

**3. Uncertainty changes behaviour, not just colour.** Needs-review prices cannot win a line in an award scenario until the buyer resolves them; the scenario lists what was held back. Conditional discounts are shown but not applied by default. "Freight extra" with no amount blocks nothing but is stated on every answer that uses those prices. A questionnaire that was not answered is "incomplete", never "cleared".

**4. Free checks before clever ones.** The best catch in the dataset (a per-piece rate typed into a per-100 column, making a ₹4.00 mailer look like ₹0.04) is caught twice without a model: the vendor's own line total disagrees, and the price is 99% below the median. The fix is offered as one click.

**5. Answers show their working, and are checked.** Each analyst answer lists the queries behind it. Every rupee figure, percentage and line count in the prose is matched against the query results; anything unmatched is called out under the answer. In testing this caught a weaker model reusing numbers from an earlier turn.

**6. Close the loop with the vendor.** One click drafts the clarification email from the open flags. The honest output of an uncertain extraction is a question, not a guess.

**7. Category: corrugated packaging**, because it has the nastiest real unit problem: converters think in ₹/kg, buyers in ₹/box, and the bridge is box weight, which nobody states. The RFx co-pilot therefore captures dimensions and board spec so a weight can be calculated and shown as an assumption.

## Measured, with caveats

One live run over the five fabricated responses (145 prices): 138 right, 7 wrong, **all 7 flagged for review, 0 silently wrong, 0 invented**. This is one run on data I fabricated myself, so it shows the checks work on the traps I built in, not field accuracy. `scripts/eval.py` reproduces it.

## Left out on purpose

- **Real email.** Stubbed, as the brief allows. Inbound parsing is plumbing.
- **Bounding boxes on the photo.** The drawer shows the whole image beside the reading. Per-cell crops would be better and are the first thing I would add.
- **Landed-cost modelling.** Freight is a buyer-entered percentage; GST, payment-term cost of money and one-time tooling charges are surfaced as flags, not priced in.
- **Multi-round negotiation, approvals, users and roles, more than one RFx.**
- **Model fine-tuning or OCR pipelines.** A general multimodal model plus verification was enough at this size.

## Where I think the better problem is

The brief frames this as reading messy responses. Having built it, I think the bigger win is upstream and downstream of reading. Upstream: most of the ambiguity (per kg vs per box, freight basis, which lines a rate covers) exists because the RFx let it exist, so the co-pilot should make the comparison basis explicit before anything is sent. Downstream: the days are lost not in retyping but in the back-and-forth to resolve doubt. A system that turns every flag into a specific question to the right vendor, tracks the reply, and re-levels automatically deletes more of the week than a better parser does.
