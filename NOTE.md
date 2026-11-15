# Level: what I decided, and what I left out

**The bet.** A buyer's week on a quote round is not lost retyping prices. It is lost in the back-and-forth to resolve doubt: per kg or per box, which lines a group rate covers, whether freight is in. So the product turns every doubt into a specific question to the right vendor, tracks the reply, and re-levels the comparison when it arrives.

The brief frames the problem as reading messy responses. Having built it, I think the larger problem is resolving doubt with vendors after quotes arrive, and preventing that ambiguity in the RFx before it goes out.

**The user** is a category buyer running an annual rate contract for corrugated packaging: about ₹4 crore a year, 30 lines, five vendors.

**The week deleted** means no rekeying five formats, no hunting for the wrong number among 150, no chasing questions by hand. The buyer sends one generated question list per vendor, pastes the answer, and the grid re-levels.

**At award the buyer hands the VP** the scenario (winners by line, split by vendor, total, saving against last year, cost of the constraints), the assumptions it rests on, and what was held back from winning and why. One Excel export carries all three.

## Decisions

**1. The model reads. Code computes.** The model copies what the vendor wrote, with verbatim evidence. Conversion, FX, discounts, freight, totals and questionnaire scoring are deterministic code. The analyst writes SQL or calls an exact award engine.

**2. No confidence percentages.** Each cell has a status built from named reasons: *verified*, *assumed* (rests on a stated FX rate, calculated weight or last year's price), or *needs review*. An assumption shared by every price from a vendor, like Pacific Kraft's USD rate, is one issue on that vendor; colour is kept for cells that need a look. The status still counts it.

**3. Uncertainty changes behaviour, not just colour.** Needs-review prices cannot win a line in an award scenario; the scenario lists what was held back. A vendor with freight extra and no amount is quoting ex-works against delivered prices, so all its prices are held back until a freight percentage is set or stated. Conditional discounts are not applied by default. An unanswered question, or an ISO certificate claimed but not attached, leaves quality "incomplete", never "cleared".

**4. Free checks before clever ones.** A per-piece rate typed into a per-100 column (₹4.00 showing as ₹0.04) is caught without a model: the vendor's own line total disagrees.

**5. Answers show their working, and are checked.** Each analyst answer lists its queries, and every figure in the prose is matched against the results. Numbers can match and still belong to the wrong vendor: in testing the model filtered on Pacific Kraft's id, labelled it Shree Balaji, and every figure traced. SQL now sees vendor names only. A second check flags a query that filters on one vendor but labels it (comment, column or table alias) as another, and a vendor named next to prices that no price-returning query of that turn filtered on or returned. It is pattern matching. A fallback model answering is called out.

**6. Close the loop.** One click drafts the clarification email from the open flags. The buyer pastes the reply, the model re-reads the whole thread, the comparison re-levels and changed cells are marked. In the demo, Om Sai's reply turns ten red cells into usable prices and states freight, so its prices stop being held back. Its quality stays incomplete: it claims ISO 9001 but sends no certificate, and the next clarification asks for one. The product does not clear a vendor on a claim.

**7. Category: corrugated packaging**, for the nastiest unit problem: converters quote ₹/kg, buyers buy per box, and the bridge is a box weight nobody states. The co-pilot captures dimensions so the weight is calculated and shown as an assumption.

## Measured, with caveats

One live run over the five responses (145 prices): 138 right, 7 wrong, all 7 flagged, 0 silently wrong, 0 invented. After Om Sai's reply was re-read, 144 prices right; the one left is the flagged ₹0.04. This is one run on data I fabricated, with a synthetic photo: it shows the checks catch my own traps, not field accuracy. `scripts/eval.py` reproduces it.

## Left out on purpose

- **Real email.** Stubbed; replies are pasted in.
- **Changing a sent RFx.** It is locked once sent; a change means a new draft. Addenda would be the proper fix.
- **Landed cost, negotiation, approvals, roles, multiple RFx.**
- **Prevention upstream.** Most ambiguity exists because the RFx allowed it. The co-pilot pushes for an explicit price basis but does not refuse to send without one.
