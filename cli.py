"""Run the brain from the terminal: python cli.py ["question"] [--month 2026-09] [--sync]"""
import argparse

from brain import config
from brain.pipeline import CompanyBrain

p = argparse.ArgumentParser()
p.add_argument("question", nargs="?", default=config.DEFAULT_QUESTION)
p.add_argument("--month")
p.add_argument("--sync", action="store_true", help="force re-ingest of documents into HydraDB")
args = p.parse_args()

brain = CompanyBrain()
if args.sync:
    print(brain.sync(force=True))
r = brain.ask(args.question, args.month)
a = r.answer
print(f"Retrieval: {r.retrieval_backend} | LLM: {r.llm_backend}\n")
print(r.metrics_text, "\n")
print("HEADLINE:", a.headline, "\n\n" + a.summary + "\n")
for title, claims in (("DRIVERS", a.drivers), ("WATCH NEXT MONTH", a.watch_next_month)):
    print(title)
    for c in claims:
        print(f"  - [{c.evidence_status}] {c.title}: {c.explanation}  {c.sources}")
print("\nEVIDENCE GAPS:", *a.evidence_gaps or ["none"], sep="\n  - ")
print("SOURCE CONFLICTS:", *a.source_conflicts or ["none"], sep="\n  - ")
print("\nEVIDENCE:")
for e in r.evidence:
    print(f"  [{e.eid}] {e.label} (for: {", ".join(e.retrieved_for)}, score {e.score})")
for n in r.validation_notes + r.warnings:
    print("NOTE:", n)
