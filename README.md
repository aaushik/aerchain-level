# RFx assistant

Take-home for Aerchain: "Kill the Quote Spreadsheet". A buyer drafts an RFx by talking, vendors reply in any format, every reply lands in one like-for-like comparison, and the buyer interrogates it in plain language through to an award.

"Levelling" is what procurement calls putting bids on a common basis. That step is the product.

## Run it

```bash
uv sync                                   # install
uv run python scripts/make_dataset.py     # fabricate the RFx and the five vendor replies (already committed under data/)
uv run uvicorn app.main:app --port 8000   # open http://localhost:8000
```

Set `LEVEL_WORK_DIR=/some/dir` to keep the database, uploads and exports somewhere else (for example to test against a copy without touching a live session).

`.env` needs `GEMINI_API_KEY` (default) or `ANTHROPIC_API_KEY` with `LLM_PROVIDER=claude`. Models are set with `GEMINI_MODEL` / `CLAUDE_MODEL`. If the Gemini model is overloaded the app fails over to `GEMINI_FALLBACK`; the UI says when a fallback model answered or read a response.

## Demo path

1. **Draft RFx**: talk to the co-pilot (starter prompts are under the chat), or click "load the finished demo RFx". Send to vendors.
2. **Responses**: "Receive vendor replies" drops the five fabricated replies into the stubbed mailbox. "Read responses" runs the real extraction, one model call per vendor. You can also add any file by hand for any vendor.
3. **Compare**: 30 lines by 5 vendors, one unit, one currency, colour only, named in the legend: Clear, Check assumption, Likely wrong, Not quoted, Photo, and the underlined lowest price that can win. Click a cell for one plain sentence on what is doubtful, the vendor's own text with the price highlighted, and the action (a suggested correction is the main button; keeping a Likely wrong price needs a typed reason, enforced by the API). Conversion steps and every flag are under "Details". Click a vendor card for its open issues, terms, questionnaire and documents, and to resolve a group of lines with the same cause in one step, or just the lines you tick under "Choose lines" (one audit entry and one undo per decision; Likely wrong prices still need a typed reason, and prices with a suggested correction are never accepted as they are). Assumptions that cover a whole column (an FX rate, a photo) are one issue on the vendor, not colour on every cell. A vendor with freight extra and no amount is held back from awards until a freight % is set. Line matches are checked against the dimensions and ply in the vendor's own item text.
4. **Clarify**: "Ask vendor" in the vendor panel drafts the questions from the open issues. "Record vendor reply" takes the vendor's answer (pasted: the stubbed inbox; Om Sai has a sample reply), re-reads the whole thread with the model and re-levels; changed cells are marked.
5. **Ask**: natural-language analysis. Each answer is short prose plus one card or table in plain rupees; "Show working" holds the checks, the allocation, the SQL or award-engine calls and the model. Figures are checked against the query results and the vendors named against the vendors the queries touched; a failed check is shown in plain words, and a fallback model is marked.

Once sent, the RFx is locked: the co-pilot answers questions about it but will not change it.

`uv run python scripts/eval.py` scores extraction against the ground truth used to fabricate the files (on list price, before discounts and freight).

## What is real and what is stubbed

| Real | Stubbed |
|---|---|
| RFx co-pilot (model edits a structured draft through tools) | SMTP: outbox and inbox are a table and a folder; vendor replies to clarifications are pasted in |
| Extraction from Excel, PDF, Word, photo, email text, and re-reading a whole clarification thread | Contract repository and vendor master (a JSON file) |
| Unit, currency, discount normalisation and all checks (deterministic code) | Auth, multi-user, multiple RFx |
| Analyst: model writes SQL and calls an exact award engine; nothing is hardcoded | |

## Layout

```
app/llm.py        provider seam: Gemini or Claude, same two operations
app/copilot.py    RFx drafting chat and its tools
app/extract.py    reads one vendor response into structured facts with evidence
app/normalize.py  the levelling: conversions, flags, statuses, questionnaire scoring
app/analyst.py    question answering: SQL snapshot, award engine, charts, exports, figure and vendor checks
app/main.py       HTTP API and Excel export
web/              single-page UI, no build step
scripts/          dataset fabrication and the extraction eval
NOTE.md           the one-page decision note
```
