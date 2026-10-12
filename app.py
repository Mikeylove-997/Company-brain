"""Streamlit front end. Run: streamlit run app.py"""
import pandas as pd
import streamlit as st

from brain import config, metrics
from brain.pipeline import CompanyBrain

st.set_page_config(page_title="Company Brain", page_icon="🧠", layout="wide")

STATUS_BADGE = {
    "supported": ":green-badge[Supported]",
    "partially_supported": ":orange-badge[Partially supported]",
    "insufficient_evidence": ":red-badge[Insufficient evidence]",
}


def md(text: str) -> str:
    """Escape '$' so dollar figures are not rendered as LaTeX math (two '$' on one line form a formula)."""
    return text.replace("$", "\\$")


@st.cache_resource
def get_brain() -> CompanyBrain:
    return CompanyBrain()


brain = get_brain()
month_options = metrics.months(brain.pl)

# ---------------------------------------------------------------- sidebar
with st.sidebar:
    st.header(config.COMPANY)
    month = st.selectbox("Reporting month", month_options, index=len(month_options) - 1,
                         format_func=metrics.month_label)
    st.subheader("Pipeline")
    st.markdown("**1. Metrics:** Python / pandas")
    if config.HYDRA_DB_API_KEY:
        synced = brain.retriever.is_synced()
        st.markdown(f"**2. Retrieval:** HydraDB `{config.HYDRA_DATABASE}` "
                    + (":green-badge[indexed]" if synced else ":orange-badge[not indexed yet]"))
        if st.button("Sync documents to HydraDB", width="stretch", help="Uploads documents only if they changed since the last sync"):
            with st.status("Syncing to HydraDB...", expanded=True) as s:
                try:
                    msg = brain.sync(log=st.write)  # re-uploads only if docs changed
                    s.update(label=msg, state="complete")
                except Exception as e:
                    s.update(label=f"Sync failed: {e}", state="error")
    else:
        st.markdown("**2. Retrieval:** :orange-badge[Local BM25 fallback]")
        st.caption("Set HYDRA_DB_API_KEY in .env to retrieve through HydraDB.")
    if config.ANTHROPIC_API_KEY:
        st.markdown(f"**3. Explanation:** Claude `{config.LLM_MODEL}`")
    else:
        st.markdown("**3. Explanation:** :orange-badge[Offline draft]")
        st.caption("Set ANTHROPIC_API_KEY in .env for LLM explanations.")
    st.markdown("**4. Guardrails:** citation and coverage checks in Python")

    st.subheader("Preloaded sources")
    for d in brain.docs.values():
        st.caption(f"📄 {d.title} · {d.date}")
    st.caption("📊 pl_monthly.csv · kpi_drivers.csv")

# ---------------------------------------------------------------- question
st.title("🧠 Financial Company Brain")
st.caption("Explains monthly P&L performance. Python computes the numbers, HydraDB retrieves the context, "
           "the LLM writes the explanation with sources, and unsupported claims are flagged.")

question = st.text_input("Ask a question", value=config.DEFAULT_QUESTION)
if st.button("Ask the brain", type="primary"):
    with st.spinner("Computing metrics, retrieving evidence, drafting the explanation..."):
        st.session_state["result"] = brain.ask(question, month)

result = st.session_state.get("result")
if result is None:
    st.info("Press **Ask the brain** to run the preloaded question.")
    st.stop()
if result.month != month:
    st.warning(f"Showing results for {metrics.month_label(result.month)}. Ask again to refresh for the selected month.")

m, a = result.metrics, result.answer
for w in result.warnings:
    st.warning(w)

# ---------------------------------------------------------------- headline numbers
summary = m.summary.set_index("metric")
cols = st.columns(4)
for col, name in zip(cols, ["Net Revenue", "Gross Profit", "Operating Expenses", "Operating Profit"]):
    r = summary.loc[name]
    col.metric(f"{name} vs budget", metrics.fmt_usd(r.actual),
               f"{metrics.fmt_usd(r.variance)} ({metrics.fmt_pct(r.variance_pct)})",
               delta_color="inverse" if name == "Operating Expenses" else "normal",
               help=f"Budget {metrics.fmt_usd(r.budget)}")

st.caption(f"Retrieval: **{result.retrieval_backend}** · Explanation: **{result.llm_backend}** · "
           f"{len(result.evidence)} passages · "
           + " · ".join(f"{k.replace('_s', '')} {v:.1f}s" for k, v in result.timings.items()))

evidence_by_id = {e.eid: e for e in result.evidence}


def render_claim(c):
    with st.container(border=True):
        st.markdown(f"**{md(c.title)}** {STATUS_BADGE[c.evidence_status]}")
        st.markdown(md(c.explanation))
        refs = [s for s in c.sources if s in evidence_by_id]
        if "METRICS" in c.sources:
            st.caption("📊 METRICS: figures computed in Python from pl_monthly.csv / kpi_drivers.csv")
        for s in refs:
            e = evidence_by_id[s]
            with st.expander(f"{s} · {md(e.label)}"):
                st.markdown(md(e.text))


tab_answer, tab_fin, tab_ev, tab_src = st.tabs(["Answer", "Financials", "Evidence", "Source files"])

with tab_answer:
    st.subheader(md(a.headline))
    st.markdown(md(a.summary))

    left, right = st.columns([3, 2])
    with left:
        st.markdown("#### Why profit moved")
        for c in a.drivers:
            render_claim(c)
    with right:
        st.markdown(f"#### Watch in {metrics.next_month_label(result.month)}")
        for c in a.watch_next_month:
            render_claim(c)
        st.markdown("#### Evidence gaps")
        if a.evidence_gaps:
            for g in a.evidence_gaps:
                st.error(md(g), icon="🔍")
        else:
            st.success("Every driver is backed by a cited source.")
        if a.source_conflicts:
            st.markdown("#### Sources disagree")
            for c in a.source_conflicts:
                st.info(md(c), icon="⚖️")
    with st.expander(f"Guardrail checks ({len(result.validation_notes)} adjustments)"):
        st.write(result.validation_notes or "All citations resolved to retrieved passages and every material driver was covered.")

with tab_fin:
    st.markdown(f"#### Line-item variance - {metrics.month_label(result.month)}")
    lines = m.lines[["line_item", "actual", "budget", "variance", "variance_pct", "profit_impact"]].copy()
    lines["material"] = lines["line_item"].isin(m.drivers["line_item"])
    st.dataframe(
        lines.style.format({"actual": "${:,.0f}", "budget": "${:,.0f}", "variance": "${:,.0f}",
                            "variance_pct": "{:+.1f}%", "profit_impact": "${:,.0f}"})
        .map(lambda v: f"color: {'#1a7f37' if v >= 0 else '#cf222e'}", subset=["profit_impact"]),
        hide_index=True, width="stretch")
    st.markdown("#### Operating KPIs")
    st.dataframe(m.kpis[["metric", "actual", "budget", "variance_pct"]].style.format(
        {"actual": "{:,.1f}", "budget": "{:,.1f}", "variance_pct": "{:+.1f}%"}),
        hide_index=True, width="stretch")
    y = m.ytd
    st.caption(f"YTD operating profit: {metrics.fmt_usd(y['actual'])} vs budget {metrics.fmt_usd(y['budget'])} "
               f"({metrics.fmt_pct(y['variance_pct'])})")
    with st.expander("Exact metrics block sent to the LLM"):
        st.code(result.metrics_text, language=None)

with tab_ev:
    st.caption("Passages retrieved for the question, for each material driver, and for next-month risks. "
               "The LLM may only cite these IDs.")
    for e in result.evidence:
        with st.expander(f"{e.eid} · {md(e.label)} · score {e.score:.2f}"):
            st.caption(f"{e.doc_type} · {e.date} · via {e.backend} · retrieved for: {', '.join(e.retrieved_for)}")
            st.markdown(md(e.text))

with tab_src:
    doc_id = st.selectbox("Document", list(brain.docs), format_func=lambda d: brain.docs[d].title)
    d = brain.docs[doc_id]
    st.caption(f"{d.doc_type} · {d.date} · {d.author}")
    st.markdown(md(d.body))
    st.markdown("#### pl_monthly.csv")
    st.dataframe(pd.read_csv(config.PL_PATH), hide_index=True, width="stretch")
