# Financial Company Brain

**Explains why profit missed budget in seconds: Python does the math, HydraDB finds the reasons in company documents, and every claim cites its source. When no document explains a number, it says so.**

Built for a fictional outdoor apparel company, Ridgeline Outfitters. All financials and documents are synthetic.

## Try it

**Live app:** _link coming_

1. Click **Ask the brain** to run the preloaded question: *"Why was profit below budget, and what should management watch next month?"* The first run takes 10–20 seconds.
2. **Answer** tab: the four headline numbers, each driver of the September miss with its evidence rating, and what to watch in October.
3. **Evidence** tab: the exact document sections HydraDB retrieved, labeled "Document > Section (date)".
4. **Financials** tab: the line-item variances and the exact block of numbers sent to the LLM.

Things to look for:
- **Software & IT** is $17.8K over budget and no document explains it, so it is marked *Insufficient evidence* and listed under Evidence gaps instead of getting a made-up reason.
- **Guardrail checks** (under the answer) lists every correction Python made to the answer.

> **Current status:** the live app runs without an LLM key, so the explanation is an *offline draft* that pairs each driver with its best keyword-matched passage. It is labeled on screen. Metrics, HydraDB retrieval and the guardrails are fully live; adding `ANTHROPIC_API_KEY` switches on the written explanation.

## How it works

```
pl_monthly.csv, kpi_drivers.csv ─► brain/metrics.py     1. Python computes variances, margins, YTD and every line
                                        │                  that moved profit by $10K or more ("drivers")
                                        ▼
data/docs/*.md ─► HydraDB ◄──── brain/retrieval.py      2. One search for the question, one per driver, one for
   (21 sections)                        │                  next-month risks. Hybrid search + graph context.
                                        ▼
                                  brain/explain.py      3. The LLM writes a structured answer. It may only quote
                                        │                  Python's numbers and must cite a passage per claim.
                                        ▼
                                  explain.validate()    4. Python removes citations to passages that were never
                                        │                  retrieved, downgrades uncited claims, flags skipped drivers
                                        ▼
                                      app.py            Streamlit UI
```

- **Numbers come only from Python.** The LLM never calculates.
- **Section-level evidence.** Each document is stored in HydraDB as one source per section (id `doc#section`), so every citation points to a specific section rather than a whole file.
- **Evidence rating per claim:** supported, partially supported, or insufficient evidence.
- **Fallback:** if HydraDB is unreachable or no key is set, retrieval switches to a local keyword search (BM25). The app always shows which backend answered.

## Run it locally

Requires **Python 3.12+**.

```bash
git clone https://github.com/Mikeylove-997/Company-brain.git && cd Company-brain
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env          # optional: add HYDRA_DB_API_KEY and/or ANTHROPIC_API_KEY
.venv/bin/streamlit run app.py
```

Both keys are optional. With no keys, the app runs end to end on the local fallback and the offline draft.

With your own `HYDRA_DB_API_KEY`, delete `.hydra_state.json` first (it records the sync to the original HydraDB account). The first question then uploads the 21 sections to your HydraDB database `ridgeline_company_brain`, collection `finance_docs`.

Other entry points:

```bash
.venv/bin/python cli.py                       # same pipeline in the terminal
.venv/bin/python -m pytest -q tests           # 9 tests: metric tie-outs, retrieval, guardrails, end to end
.venv/bin/python data/generate_financials.py  # regenerate the synthetic CSVs (seeded)
```

## Files

| Path | Purpose |
|---|---|
| `data/financials/` | Synthetic monthly P&L and KPIs, actual vs budget, Jan–Sep 2026 |
| `data/docs/` | 3 analyst reports (FP&A, logistics, sector) and 2 management meeting notes |
| `brain/metrics.py` | All financial calculations |
| `brain/documents.py` | Loads documents and splits them into sections |
| `brain/retrieval.py` | HydraDB sync and search, plus the local fallback |
| `brain/explain.py` | LLM prompt and answer schema, offline draft, citation guardrails |
| `brain/pipeline.py` | Runs the four steps end to end |
| `app.py` | Streamlit UI |

## What's next

- Connect the LLM key for written explanations.
- A Python check that every dollar figure in the answer matches the computed metrics.
- A waterfall chart from budgeted to actual profit, one bar per driver.
- Follow-up questions, and uploading new notes through the app straight into HydraDB.
