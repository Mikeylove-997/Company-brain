# Handoff

## Current state (2026-10-12)
- Full flow works: Python metrics -> HydraDB retrieval -> LLM explanation -> Python citation checks -> Streamlit UI.
- HydraDB is live: database `ridgeline_company_brain`, collection `finance_docs`, **21 per-section sources**
  (id `"{doc_id}#{section-slug}"`, each prefixed with "Title > Section" + type/date/author; format `sections-v1`).
  Sync state in `.hydra_state.json` (format + content hash + ids + `stale`).
- Query: `query_by="hybrid"`, `graph_context=True`, `max_results=2k`, then near-duplicate drop (token Jaccard >= 0.8)
  in both `HydraRetriever.search` and the pipeline. Pipeline k=4 per query -> ~13 distinct section passages.
- `Evidence.section` + `Evidence.label` ("Doc title > Section (date)") shown in UI, CLI and the LLM prompt.
- Stale sources: sync ingests first, then deletes ids from earlier syncs that are no longer in the corpus. HydraDB
  refuses (409 SOURCE_PROCESSING) while a source is processing; those ids stay in `stale`, are skipped in search, and
  deletion is retried on every sync (incl. the Sync button). **Open:** the 5 old whole-file sources were stuck at
  `queued` on 2026-10-12, so they are still in HydraDB (user approved deleting them). Press Sync later to finish.
- No LLM key yet: answers use the offline keyword draft (weak: tends to quote the FP&A "Summary" section for several
  drivers, and misses DTC because the keyword is in the stripped heading). Swap only `brain/explain.py`.
- 9 pytest tests pass: `.venv/bin/python -m pytest -q tests`.

## Backlog
1. Connect an LLM key (blocker for the real demo).
2. Python check that every $ figure in the LLM text appears in the metrics block.
3. Waterfall chart budget profit -> actual profit by driver.
4. Follow-up chat questions; upload a new note via UI straight into HydraDB.
