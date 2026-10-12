import pytest

from brain import config, explain, metrics
from brain.documents import load_documents
from brain.explain import BrainAnswer, Claim
from brain.pipeline import CompanyBrain
from types import SimpleNamespace

from brain.retrieval import Evidence, HydraRetriever, LocalRetriever

MONTH = "2026-09"


@pytest.fixture(scope="module")
def pl():
    return metrics.load_pl()


@pytest.fixture(scope="module")
def m(pl):
    return metrics.compute(pl, metrics.load_kpis(), MONTH)


def test_operating_profit_ties_to_line_items(pl, m):
    df = pl[pl.month == MONTH]
    sign = df.category.map({"revenue": 1, "cogs": -1, "opex": -1})
    op = m.summary.set_index("metric").loc["Operating Profit"]
    assert op.actual == pytest.approx((df.actual * sign).sum())
    assert op.budget == pytest.approx((df.budget * sign).sum())
    # Driver profit impacts plus immaterial lines explain the whole operating profit variance.
    assert m.lines.profit_impact.sum() == pytest.approx(op.variance)


def test_profit_impact_sign_convention(m):
    lines = m.lines.set_index("line_item")
    assert lines.loc["Revenue - Wholesale", "profit_impact"] < 0  # revenue below budget hurts
    assert lines.loc["COGS - Freight & Fulfillment", "profit_impact"] < 0  # cost above budget hurts
    assert lines.loc["COGS - Materials & Manufacturing", "favorable"]


def test_drivers_are_material_and_sorted(m):
    assert (m.drivers.profit_impact.abs() >= config.MATERIALITY_USD).all()
    assert list(m.drivers.profit_impact) == sorted(m.drivers.profit_impact)
    assert "OpEx - Rent & Facilities" not in set(m.drivers.line_item)


def test_local_retrieval_finds_freight_doc():
    hits = LocalRetriever(load_documents()).search("freight carrier surcharge expedited air", k=3)
    assert any("freight" in h.text.lower() and "surcharge" in h.text.lower() for h in hits)


def test_freight_query_returns_freight_section():
    hits = LocalRetriever(load_documents()).search("Why was COGS - Freight & Fulfillment over budget? "
                                                    "freight fulfillment carrier surcharge expedited air shipping", k=3)
    assert (hits[0].doc_id, hits[0].section) == ("fpa_variance_commentary_2026-09", "Freight and fulfillment")
    assert hits[0].label.startswith("FP&A Variance Commentary - September 2026 (preliminary) > Freight and fulfillment")


def test_hydra_section_ids_map_back_to_doc_and_section():
    docs = load_documents()
    r = object.__new__(HydraRetriever)  # no client needed to resolve ids
    r.docs = docs
    doc = docs["logistics_freight_outlook_q4-2026"]
    sid = doc.section_id("Expected cost path")
    assert sid == "logistics_freight_outlook_q4-2026#expected-cost-path"
    chunk = SimpleNamespace(id=sid, additional_metadata={}, metadata=None, source_title=None)
    assert r._locate(chunk) == (doc, "Expected cost path")
    sources = {sid for sid, _, _ in r._sources()}
    assert len(sources) == sum(1 for d in docs.values() for _, t in d.sections if t)


def _ev(eid):
    return Evidence(doc_id="d", title="t", doc_type="x", date="", text="...", score=1, backend="test", eid=eid)


def test_validate_strips_fake_citations_and_downgrades(m):
    drivers = [Claim(title=line, explanation="x", sources=["E1"], evidence_status="supported")
               for line in m.drivers.line_item]
    drivers[0].sources = ["E99"]  # hallucinated citation
    ans = BrainAnswer(headline="h", summary="s", drivers=drivers, watch_next_month=[],
                      evidence_gaps=[], source_conflicts=[])
    notes = explain.validate(ans, m, [_ev("E1")])
    assert ans.drivers[0].sources == []
    assert ans.drivers[0].evidence_status == "insufficient_evidence"
    assert any("E99" in n for n in notes)
    assert ans.drivers[1].evidence_status == "supported"


def test_validate_flags_uncovered_driver(m):
    ans = BrainAnswer(headline="h", summary="s", drivers=[], watch_next_month=[], evidence_gaps=[],
                      source_conflicts=[])
    explain.validate(ans, m, [])
    assert len(ans.evidence_gaps) == len(m.drivers)


def test_end_to_end_offline_flags_unexplained_software_spend(monkeypatch):
    monkeypatch.setattr(config, "ANTHROPIC_API_KEY", None)
    monkeypatch.setattr(config, "HYDRA_DB_API_KEY", None)
    r = CompanyBrain().ask()
    assert r.month == MONTH and r.evidence
    software = next(c for c in r.answer.drivers if c.title == "OpEx - Software & IT")
    assert software.evidence_status == "insufficient_evidence"
    cited = {s for c in r.answer.drivers for s in c.sources} - {"METRICS"}
    assert cited <= {e.eid for e in r.evidence}
