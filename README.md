# Level

Take-home for Aerchain: "Kill the Quote Spreadsheet". A buyer drafts an RFx by talking, vendors reply in any format, every reply lands in one like-for-like comparison, and the buyer interrogates it in plain language through to an award.

"Levelling" is what procurement calls putting bids on a common basis. That step is the product.

## Run it

```bash
uv sync                                   # install
uv run python scripts/make_dataset.py     # fabricate the RFx and the five vendor replies (already committed under data/)
uv run uvicorn app.main:app --port 8000   # open http://localhost:8000
```

`.env` needs `GEMINI_API_KEY` (default) or `ANTHROPIC_API_KEY` with `LLM_PROVIDER=claude`. Models are set with `GEMINI_MODEL` / `CLAUDE_MODEL`.

## Demo path

1. **Draft RFx**: talk to the co-pilot (starter prompts are under the chat), or click "load the finished demo RFx". Send to vendors.
2. **Responses**: "Receive vendor replies" drops the five fabricated replies into the stubbed mailbox. "Read responses" runs the real extraction, one model call per vendor. You can also add any file by hand for any vendor.
3. **Compare**: 30 lines by 5 vendors, one unit, one currency. Click any cell for what the vendor wrote, each conversion step, every reason not to trust it, and the source. Confirm, correct or exclude; decisions are logged.
4. **Ask**: natural-language analysis. Each answer shows the SQL or award-engine call behind it and checks its own figures against the query results.

`uv run python scripts/eval.py` scores extraction against the ground truth used to fabricate the files.

## What is real and what is stubbed

| Real | Stubbed |
|---|---|
| RFx co-pilot (model edits a structured draft through tools) | SMTP: outbox and inbox are a table and a folder |
| Extraction from Excel, PDF, Word, photo, email text | Contract repository and vendor master (a JSON file) |
| Unit, currency, discount normalisation and all checks (deterministic code) | Auth, multi-user, multiple RFx |
| Analyst: model writes SQL and calls an exact award engine; nothing is hardcoded | |

## Layout

```
app/llm.py        provider seam: Gemini or Claude, same two operations
app/copilot.py    RFx drafting chat and its tools
app/extract.py    reads one vendor response into structured facts with evidence
app/normalize.py  the levelling: conversions, flags, statuses, questionnaire scoring
app/analyst.py    question answering: SQL snapshot, award engine, charts, exports, figure tracing
app/main.py       HTTP API and Excel export
web/              single-page UI, no build step
scripts/          dataset fabrication and the extraction eval
NOTE.md           the one-page decision note
```
