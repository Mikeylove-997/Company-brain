"""Context retrieval. HydraDB is the retrieval layer; a local BM25 index is a clearly-labelled
fallback so the demo still runs when HydraDB is unreachable or no key is configured."""
import json
import math
import re
import time
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from . import config
from .documents import Document, corpus_fingerprint


@dataclass
class Evidence:
    doc_id: str
    title: str
    doc_type: str
    date: str
    text: str
    score: float
    backend: str
    section: str = ""  # heading within the document, when the passage comes from one section
    retrieved_for: list[str] = field(default_factory=list)  # pipeline query labels that surfaced this passage
    eid: str = ""  # E1, E2... assigned by the pipeline after de-duplication

    @property
    def label(self) -> str:
        """'Doc title > Section (date)' for display."""
        where = f"{self.title} > {self.section}" if self.section else self.title
        return f"{where} ({self.date})" if self.date else where


def near_duplicate(a: Evidence, b: Evidence, threshold=0.8) -> bool:
    """Same passage surfaced twice (overlapping chunks, or one section returned by two queries)."""
    ta, tb = set(_tokens(a.text)), set(_tokens(b.text))
    return bool(ta and tb) and len(ta & tb) / len(ta | tb) >= threshold


# ---------------------------------------------------------------- HydraDB


SEARCHABLE_STATUSES = {"graph_creation", "completed"}
INGEST_FORMAT = "sections-v1"  # bump when the way documents are split for upload changes


class HydraRetriever:
    name = "HydraDB"

    def __init__(self, docs: dict[str, Document]):
        from hydra_db import HydraDB

        self.docs = docs
        self.client = HydraDB(token=config.HYDRA_DB_API_KEY)
        self.db, self.collection = config.HYDRA_DATABASE, config.HYDRA_COLLECTION

    def _state(self) -> dict:
        try:
            return json.loads(config.HYDRA_STATE_PATH.read_text())
        except (FileNotFoundError, json.JSONDecodeError):
            return {}

    def is_synced(self) -> bool:
        s = self._state()
        return (s.get("database") == self.db and s.get("format") == INGEST_FORMAT
                and s.get("fingerprint") == corpus_fingerprint(self.docs))

    def _ensure_database(self, log, timeout=180):
        try:
            self.client.databases.create(database=self.db)
            log(f"Created HydraDB database '{self.db}'")
        except Exception as e:  # already exists is the normal case on re-runs
            log(f"Database create skipped ({type(e).__name__}); assuming '{self.db}' exists")
        start = time.time()
        while not self.client.databases.status(database=self.db).data.infra.ready_for_ingestion:
            if time.time() - start > timeout:
                raise TimeoutError("HydraDB database not ready for ingestion")
            time.sleep(5)

    def _sources(self) -> list[tuple[str, tuple, dict]]:
        """One HydraDB source per document section, so search returns section-sized passages."""
        out = []
        for d in self.docs.values():
            for heading, text in d.sections:
                if not text:
                    continue
                sid = d.section_id(heading)
                file = (f"{sid.replace('#', '__')}.md", d.section_text(heading, text).encode(), "text/markdown")
                meta = {"id": sid, "additional_metadata": {"doc_id": d.doc_id, "title": d.title, "section": heading,
                                                           "doc_type": d.doc_type, "date": d.date}}
                out.append((sid, file, meta))
        return out

    def _purge(self, stale: list[str], log) -> list[str]:
        """Delete sources an earlier sync uploaded that are no longer in the corpus. HydraDB refuses to delete
        a source that is still processing; those ids are returned so they can be skipped and retried."""
        if not stale:
            return []
        resp = self.client.context.delete(database=self.db, collection=self.collection, type="knowledge",
                                          ids=stale, hydra_db_delete_status="legacy")
        left = sorted(r.id for r in resp.data.results or [] if not r.deleted)
        log(f"Removed {len(stale) - len(left)} stale source(s) from HydraDB"
            + (f"; {len(left)} still processing, will retry on next sync" if left else ""))
        return left

    def _stale(self) -> set[str]:
        return set(self._state().get("stale", []))

    def sync(self, force=False, log=print, timeout=300) -> str:
        """Ingest the preloaded documents (one source per section) once per corpus version."""
        state = self._state()
        if self.is_synced() and not force:
            if state.get("stale"):
                state["stale"] = self._purge(state["stale"], log)
                config.HYDRA_STATE_PATH.write_text(json.dumps(state))
            return "HydraDB index already up to date"
        self._ensure_database(log)
        sources = self._sources()
        resp = self.client.context.ingest(type="knowledge", database=self.db, collection=self.collection,
                                          documents=[f for _, f, _ in sources], upsert="true",
                                          document_metadata=json.dumps([m for _, _, m in sources]))
        ids = [r.id for r in resp.data.results]
        log(f"Ingested {len(ids)} sections from {len(self.docs)} documents; waiting for indexing...")
        start, pending = time.time(), set(ids)
        while pending:
            if time.time() - start > timeout:
                raise TimeoutError(f"HydraDB indexing still pending for {sorted(pending)}")
            time.sleep(4)
            statuses = self.client.context.status(database=self.db, collection=self.collection,
                                                  ids=list(pending)).data.statuses
            for s in statuses:
                if s.indexing_status == "errored":
                    raise RuntimeError(f"HydraDB failed to index {s}: {getattr(s, 'error_message', '')}")
                # Text search works once a source reaches graph_creation; the graph finishes in the background.
                if s.indexing_status in SEARCHABLE_STATUSES:
                    pending.discard(s.id)
        stale = sorted((set(state.get("ids", [])) | set(state.get("stale", []))) - set(ids))
        config.HYDRA_STATE_PATH.write_text(json.dumps(
            {"database": self.db, "format": INGEST_FORMAT, "fingerprint": corpus_fingerprint(self.docs),
             "ids": ids, "stale": self._purge(stale, log)}))
        return f"Indexed {len(ids)} sections in HydraDB"

    def _locate(self, chunk) -> tuple[Document | None, str]:
        """Map a returned chunk to its document and section via the '{doc_id}#{section-slug}' source id."""
        extra = chunk.additional_metadata or {}
        candidates = [chunk.id, extra.get("id"), (chunk.metadata or {}).get("id"),
                      Path(chunk.source_title or "").stem.replace("__", "#")]
        for c in filter(None, candidates):
            doc_id, _, section_slug = c.partition("#")
            if doc_id in self.docs:
                doc = self.docs[doc_id]
                return doc, doc.section_for(section_slug) or extra.get("section", "")
        return None, ""

    def search(self, query: str, k=4) -> list[Evidence]:
        # Ask for extra results so near-duplicate chunks can be dropped and still leave k passages.
        resp = self.client.query(database=self.db, collections=[self.collection], type="knowledge",
                                 query=query, max_results=k * 2, query_by="hybrid", graph_context=True,
                                 response_format="structured")
        out, stale = [], self._stale()
        for c in resp.data.chunks or []:
            if c.id in stale:  # superseded source HydraDB has not let us delete yet
                continue
            doc, section = self._locate(c)
            text = (c.chunk_content or "").strip()
            if doc and section and text.startswith(f"{doc.title} > {section}"):
                text = text.partition("\n\n")[2].strip()  # header added at ingest; the UI shows it as a label
            e = Evidence(
                doc_id=doc.doc_id if doc else (c.source_title or c.id or "unknown"),
                title=doc.title if doc else (c.source_title or "Untitled source"),
                doc_type=doc.doc_type if doc else (c.source_type or "document"),
                date=doc.date if doc else "", text=text, section=section,
                score=c.relevancy_score or 0.0, backend=self.name)
            if text and not any(near_duplicate(e, o) for o in out):
                out.append(e)
            if len(out) == k:
                break
        return out


# ---------------------------------------------------------------- Local fallback

_STOP = set("a an and are as at be by for from has have in is it its of on or our that the this to was were will with "
            "we not but than about vs".split())


def _tokens(text: str) -> list[str]:
    return [t for t in re.findall(r"[a-z0-9%$]+", text.lower()) if t not in _STOP]


class LocalRetriever:
    """BM25 over document sections. Used only when HydraDB is unavailable."""

    name = "Local BM25 (fallback)"

    def __init__(self, docs: dict[str, Document], k1=1.5, b=0.75):
        self.chunks = [(d, h, t) for d in docs.values() for h, t in d.sections if t]
        self.toks = [_tokens(f"{h}\n{t}") for _, h, t in self.chunks]
        self.avg = sum(map(len, self.toks)) / len(self.toks)
        df = Counter(t for toks in self.toks for t in set(toks))
        n = len(self.toks)
        self.idf = {t: math.log(1 + (n - c + 0.5) / (c + 0.5)) for t, c in df.items()}
        self.k1, self.b = k1, b

    def is_synced(self) -> bool:
        return True

    def sync(self, force=False, log=print) -> str:
        return "Local index built in memory"

    def search(self, query: str, k=4) -> list[Evidence]:
        q = _tokens(query)
        scored = []
        for (doc, heading, text), toks in zip(self.chunks, self.toks):
            tf = Counter(toks)
            s = sum(self.idf.get(t, 0) * tf[t] * (self.k1 + 1) /
                    (tf[t] + self.k1 * (1 - self.b + self.b * len(toks) / self.avg)) for t in q if t in tf)
            if s > 0:
                scored.append((s, doc, heading, text))
        scored.sort(key=lambda x: -x[0])
        return [Evidence(doc_id=d.doc_id, title=d.title, doc_type=d.doc_type, date=d.date, text=t, section=h,
                         score=round(s, 2), backend=self.name) for s, d, h, t in scored[:k]]


def get_retriever(docs: dict[str, Document]):
    if config.HYDRA_DB_API_KEY:
        return HydraRetriever(docs)
    return LocalRetriever(docs)
