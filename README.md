# Financial Company Brain (hackathon prototype)

Explains monthly P&L performance for **Ridgeline Outfitters** (fictional, all data synthetic).
Preloaded question: *"Why was profit below budget, and what should management watch next month?"*

## How it works

```
pl_monthly.csv, kpi_drivers.csv ──► brain/metrics.py    Python computes variances, margins, YTD and material drivers
                                         │ (drivers become targeted search queries)
data/docs/*.md ──► HydraDB ◄──── brain/retrieval.py     1 query for the question + 1 per driver + 1 for next-month risks
                                         │ evidence passages E1..En
                                    brain/explain.py    Claude writes a structured answer; every claim cites E-ids or METRICS
                                         │
                                    explain.validate()  Python strips unknown citations, downgrades uncited claims to
                                                        "insufficient evidence", flags drivers the answer skipped
                                         ▼
                                       app.py           Streamlit UI
```

- **Numbers come only from Python.** The LLM gets a metrics block and is told to quote, not calculate.
- **Evidence status per claim:** supported / partially supported / insufficient evidence.
- **Built-in test cases in the data:** the September *Software & IT* overspend ($17.8K) has no supporting document, so it should be flagged
  as insufficient evidence. The freight outlook and the sector note disagree on when surcharges end, so that should show up as a source conflict.

## Run

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env          # add ANTHROPIC_API_KEY and HYDRA_DB_API_KEY
.venv/bin/streamlit run app.py
```

On first question (or via **Sync documents to HydraDB** in the sidebar) the five docs are ingested into HydraDB
database `ridgeline_company_brain`, collection `finance_docs`, then cached by content hash in `.hydra_state.json`.

Without keys the app still runs end to end with labelled fallbacks (local BM25 retrieval, keyword-matched offline draft) so
the UI can be demoed; the sidebar and result caption always show which backend produced the answer.

Other entry points:

```bash
.venv/bin/python cli.py                       # same pipeline in the terminal
.venv/bin/python cli.py --sync                # force re-ingest into HydraDB
.venv/bin/python -m pytest -q tests           # metric tie-outs, guardrails, offline end-to-end
.venv/bin/python data/generate_financials.py  # regenerate synthetic CSVs (seeded)
```

## Files

| Path | Purpose |
|---|---|
| `data/financials/` | Synthetic monthly P&L and KPI actuals vs budget, Jan–Sep 2026 |
| `data/docs/` | 3 analyst reports (FP&A, logistics, external sector) + 2 management meeting notes |
| `brain/metrics.py` | All financial calculations |
| `brain/retrieval.py` | HydraDB ingest/query, plus local BM25 fallback |
| `brain/explain.py` | Claude prompt, structured output schema, offline draft, citation validation |
| `brain/pipeline.py` | Orchestration |
| `app.py` | Streamlit UI |

## Next features (after the core flow)
Free-form questions over other months, a drill-down chart per driver, a numeric check that every number in the LLM's text
appears in the metrics block, and uploading new notes through the UI straight into HydraDB.
