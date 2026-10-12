"""LLM explanation layer: turns Python-computed metrics + retrieved evidence into a cited answer,
then checks the answer's citations in code before anything is shown."""
import re
from typing import Literal

from pydantic import BaseModel, Field

from . import config
from .metrics import MonthMetrics, fmt_pct, fmt_usd, month_label, next_month_label
from .retrieval import Evidence

Status = Literal["supported", "partially_supported", "insufficient_evidence"]


class Claim(BaseModel):
    title: str = Field(description="Line item or topic, e.g. 'COGS - Freight & Fulfillment'")
    explanation: str = Field(description="2-4 sentences. Quote numbers only from METRICS; cite evidence inline like [E2].")
    sources: list[str] = Field(description="Evidence IDs used (e.g. 'E2'), plus 'METRICS' if numbers are quoted")
    evidence_status: Status


class BrainAnswer(BaseModel):
    headline: str = Field(description="One sentence answering the question directly, with the profit variance")
    summary: str = Field(description="3-5 sentences tying the drivers together, with citations")
    drivers: list[Claim] = Field(description="One entry per material variance driver, worst first")
    watch_next_month: list[Claim] = Field(description="3-6 forward-looking items for management next month")
    evidence_gaps: list[str] = Field(description="What could not be explained from the evidence, and who/what would resolve it")
    source_conflicts: list[str] = Field(description="Places where sources disagree; empty if none")


SYSTEM_PROMPT = f"""You are the finance "Company Brain" for {config.COMPANY}. You explain monthly P&L performance to the management team.

You receive two inputs:
- METRICS: every figure, computed by Python from the general ledger. It tells you WHAT moved.
- EVIDENCE: passages retrieved from analyst reports and management meeting notes, each with an ID like [E3]. It tells you WHY.

How to answer:
- Quote numbers exactly as written in METRICS. Do not calculate, round differently, or introduce any figure that is not in METRICS or stated in an evidence passage. If you use a figure from a passage, cite that passage.
- Every causal claim needs an inline citation to the evidence ID(s) that support it. Cite "METRICS" for numbers taken from METRICS.
- Cover every driver listed under MATERIAL VARIANCE DRIVERS, including favorable ones.
- evidence_status: "supported" when the evidence directly explains the variance; "partially_supported" when it explains only part of the amount, or the link is indirect (e.g. a sector note about the industry rather than the company); "insufficient_evidence" when nothing in EVIDENCE explains it.
- When evidence is insufficient, say so plainly, do not guess a cause, and add an entry to evidence_gaps naming what document or owner would close the gap.
- Watch items must be grounded in evidence (dated events, open decisions, stated risks) or in a METRICS trend, and say what to check and why it matters for next month's profit.
- If sources disagree (e.g. on timing or outlook), describe the disagreement in source_conflicts and cite both sides.
- Write for busy executives: plain language, no filler."""


def build_user_prompt(question: str, m: MonthMetrics, metrics_text: str, evidence: list[Evidence]) -> str:
    ev = "\n\n".join(f"[{e.eid}] {e.label} [{e.doc_type}] - retrieved for: {", ".join(e.retrieved_for)}\n{e.text}"
                     for e in evidence)
    return (f"Reporting month: {month_label(m.month)}. 'Next month' means {next_month_label(m.month)}.\n\n"
            f"<metrics>\n{metrics_text}\n</metrics>\n\n<evidence>\n{ev or '(no passages retrieved)'}\n</evidence>\n\n"
            f"Question: {question}")


def explain_with_claude(question: str, m: MonthMetrics, metrics_text: str,
                        evidence: list[Evidence]) -> tuple[BrainAnswer, dict]:
    import anthropic

    client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
    response = client.beta.messages.parse(
        model=config.LLM_MODEL,
        max_tokens=16000,
        output_config={"effort": "medium"},
        # Server-side fallback: if a safety classifier declines, the API reroutes within the same call.
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": build_user_prompt(question, m, metrics_text, evidence)}],
        output_format=BrainAnswer,
    )
    if response.stop_reason == "refusal":
        raise RuntimeError("The model declined to answer this request.")
    if response.parsed_output is None:
        raise RuntimeError(f"No structured answer returned (stop_reason={response.stop_reason}).")
    usage = {"model": response.model, "input_tokens": response.usage.input_tokens,
             "output_tokens": response.usage.output_tokens}
    return response.parsed_output, usage


def explain_offline(question: str, m: MonthMetrics, evidence: list[Evidence]) -> BrainAnswer:
    """Deterministic stand-in used when no LLM key is configured. Not a substitute for the LLM:
    it just pairs each driver with its best-matching passage so the flow can be demoed end to end."""
    op = m.summary.set_index("metric").loc["Operating Profit"]
    drivers = []
    for r in m.drivers.itertuples():
        keyword = r.line_item.split(" - ")[1].split()[0].lower()  # "wholesale", "freight", "software"...
        hits = [e for e in evidence if r.line_item in e.retrieved_for and keyword in e.text.lower()]
        nums = f"{r.line_item} was {fmt_usd(r.variance)} ({fmt_pct(r.variance_pct)}) vs budget, profit impact {fmt_usd(r.profit_impact)}."
        if hits:
            sentences = re.split(r"(?<=[.!?])\s+|\n+", hits[0].text)
            quote = next(x for x in sentences if keyword in x.lower()).strip().lstrip("- ")
            drivers.append(Claim(title=r.line_item, explanation=f"{nums} Matching passage: \"{quote}\" [{hits[0].eid}]",
                                 sources=["METRICS", hits[0].eid], evidence_status="partially_supported"))
        else:
            drivers.append(Claim(title=r.line_item, explanation=f"{nums} No retrieved passage mentions this line.",
                                 sources=["METRICS"], evidence_status="insufficient_evidence"))
    watch = [Claim(title=e.title, explanation=e.text[:300] + ("..." if len(e.text) > 300 else ""),
                   sources=[e.eid], evidence_status="partially_supported")
             for e in evidence if "next-month outlook" in e.retrieved_for][:3]
    return BrainAnswer(
        headline=f"Operating profit was {fmt_usd(op.variance)} ({fmt_pct(op.variance_pct)}) vs budget in {month_label(m.month)}.",
        summary="Offline mode: no LLM key configured, so this is a keyword match between drivers and passages, "
                "not a reasoned explanation. Set ANTHROPIC_API_KEY for the real answer.",
        drivers=drivers, watch_next_month=watch,
        evidence_gaps=[c.title for c in drivers if c.evidence_status == "insufficient_evidence"],
        source_conflicts=[])


def validate(answer: BrainAnswer, m: MonthMetrics, evidence: list[Evidence]) -> list[str]:
    """Enforce the evidence rules in code rather than trusting the model. Mutates `answer`."""
    valid = {e.eid for e in evidence} | {"METRICS"}
    notes = []
    for section, claims in (("driver", answer.drivers), ("watch item", answer.watch_next_month)):
        for c in claims:
            bad = [s for s in c.sources if s not in valid]
            if bad:
                notes.append(f"Removed unknown citation(s) {bad} from {section} '{c.title}'.")
                c.sources = [s for s in c.sources if s in valid]
            if c.evidence_status != "insufficient_evidence" and not any(s != "METRICS" for s in c.sources):
                c.evidence_status = "insufficient_evidence"
                notes.append(f"Downgraded {section} '{c.title}' to insufficient evidence: no document citation.")
                if c.title not in " ".join(answer.evidence_gaps):
                    answer.evidence_gaps.append(f"{c.title}: no supporting document cited.")
    covered = " ".join(c.title for c in answer.drivers)
    for line in m.drivers["line_item"]:
        if line not in covered:
            notes.append(f"Material driver '{line}' was not addressed in the answer.")
            answer.evidence_gaps.append(f"{line}: material variance not explained.")
    return notes
