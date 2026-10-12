"""End-to-end flow: Python metrics -> targeted retrieval -> LLM explanation -> citation checks."""
import time
from dataclasses import dataclass, field

from . import config, explain, metrics
from .documents import load_documents
from .retrieval import Evidence, LocalRetriever, get_retriever, near_duplicate


def _err(e: Exception) -> str:
    """Short, readable error text (SDK errors can embed full HTTP headers)."""
    code = getattr(e, "status_code", None)
    detail = getattr(e, "body", None) or getattr(e, "message", None) or str(e)
    return f"{type(e).__name__}{f' {code}' if code else ''}: {str(detail)[:160]}"


@dataclass
class BrainResult:
    question: str
    month: str
    metrics: metrics.MonthMetrics
    metrics_text: str
    evidence: list[Evidence]
    answer: explain.BrainAnswer
    retrieval_backend: str
    llm_backend: str
    validation_notes: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    timings: dict = field(default_factory=dict)
    usage: dict = field(default_factory=dict)


class CompanyBrain:
    def __init__(self):
        self.pl, self.kpis = metrics.load_pl(), metrics.load_kpis()
        self.docs = load_documents()
        self.retriever = get_retriever(self.docs)
        self.fallback = None

    def sync(self, force=False, log=print) -> str:
        return self.retriever.sync(force=force, log=log)

    def _queries(self, question: str, m: metrics.MonthMetrics) -> list[tuple[str, str]]:
        """One query for the question, one per material driver, one for next-month risks."""
        month, nxt = metrics.month_label(m.month), metrics.next_month_label(m.month)
        qs = [("question", question)]
        for r in m.drivers.itertuples():
            direction = "over" if (r.variance > 0) else "under"
            qs.append((r.line_item, f"Why was {r.line_item} {direction} budget in {month}? "
                                    f"{metrics.DRIVER_KEYWORDS.get(r.line_item, '')}"))
        qs.append(("next-month outlook", f"What should management watch in {nxt}? risks, decisions, "
                                         f"expected dates, follow-ups, outlook"))
        return qs

    def _search(self, query: str, k: int, warnings: list[str]) -> tuple[list[Evidence], str]:
        if self.fallback is None:
            try:
                return self.retriever.search(query, k=k), self.retriever.name
            except Exception as e:
                warnings.append(f"{self.retriever.name} query failed ({_err(e)}); "
                                f"switched to local fallback retrieval.")
                self.fallback = LocalRetriever(self.docs)
        return self.fallback.search(query, k=k), self.fallback.name

    def ask(self, question: str = config.DEFAULT_QUESTION, month: str | None = None, k=4) -> BrainResult:
        month = month or metrics.months(self.pl)[-1]
        warnings, t = [], {}
        self.fallback = None

        t0 = time.time()
        m = metrics.compute(self.pl, self.kpis, month)
        metrics_text = m.to_prompt_text(self.pl)
        t["metrics_s"] = time.time() - t0

        t0 = time.time()
        if not self.retriever.is_synced():
            try:
                self.retriever.sync()
            except Exception as e:
                warnings.append(f"HydraDB sync failed ({_err(e)}).")
        evidence, backends = [], set()
        for label, q in self._queries(question, m):
            hits, backend = self._search(q, k, warnings)
            backends.add(backend)
            for e in hits:
                if not e.text:
                    continue
                kept = next((x for x in evidence if near_duplicate(x, e)), None)
                if kept is None:
                    evidence.append(e)
                    kept = e
                if label not in kept.retrieved_for:
                    kept.retrieved_for.append(label)
        for i, e in enumerate(evidence, 1):
            e.eid = f"E{i}"
        t["retrieval_s"] = time.time() - t0

        t0 = time.time()
        usage = {}
        if config.ANTHROPIC_API_KEY:
            try:
                answer, usage = explain.explain_with_claude(question, m, metrics_text, evidence)
                llm_backend = f"Claude ({usage.get('model', config.LLM_MODEL)})"
            except Exception as e:
                warnings.append(f"LLM call failed ({_err(e)}); showing offline draft.")
                answer, llm_backend = explain.explain_offline(question, m, evidence), "Offline draft (no LLM)"
        else:
            answer, llm_backend = explain.explain_offline(question, m, evidence), "Offline draft (no LLM)"
        t["llm_s"] = time.time() - t0

        notes = explain.validate(answer, m, evidence)
        return BrainResult(question=question, month=month, metrics=m, metrics_text=metrics_text, evidence=evidence,
                           answer=answer, retrieval_backend=" + ".join(sorted(backends)), llm_backend=llm_backend,
                           validation_notes=notes, warnings=warnings, timings=t, usage=usage)
