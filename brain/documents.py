"""Load the preloaded analyst reports and meeting notes (markdown with a simple front-matter header)."""
import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path

from . import config


@dataclass
class Document:
    doc_id: str
    path: Path
    title: str
    doc_type: str
    date: str
    author: str
    body: str
    sections: list[tuple[str, str]] = field(default_factory=list)  # (heading, text)

    def section_id(self, heading: str) -> str:
        """Retrieval source id for one section, e.g. 'fpa_variance_commentary_2026-09#freight-and-fulfillment'."""
        return f"{self.doc_id}#{slug(heading)}"

    def section_for(self, section_slug: str) -> str:
        """Heading whose slug matches, or '' if none does."""
        return next((h for h, _ in self.sections if slug(h) == section_slug), "")

    def section_text(self, heading: str, text: str) -> str:
        """Section text with a header line so each section is self-describing once split from its file."""
        return f"{self.title} > {heading}\n{self.doc_type} · {self.date} · {self.author}\n\n{text}"


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def _parse(path: Path) -> Document:
    raw = path.read_text()
    meta, body = {}, raw
    m = re.match(r"^---\n(.*?)\n---\n(.*)$", raw, re.S)
    if m:
        for line in m.group(1).splitlines():
            key, _, value = line.partition(":")
            meta[key.strip()] = value.strip()
        body = m.group(2)
    sections = []
    for block in re.split(r"\n(?=## )", body.strip()):
        heading, _, text = block.partition("\n")
        sections.append((heading.lstrip("# ").strip(), text.strip()))
    return Document(doc_id=path.stem, path=path, title=meta.get("title", path.stem),
                    doc_type=meta.get("doc_type", "document"), date=meta.get("date", ""),
                    author=meta.get("author", ""), body=body, sections=sections)


def load_documents(docs_dir: Path = config.DOCS_DIR) -> dict[str, Document]:
    return {p.stem: _parse(p) for p in sorted(docs_dir.glob("*.md"))}


def corpus_fingerprint(docs: dict[str, Document]) -> str:
    h = hashlib.sha256()
    for doc_id, doc in sorted(docs.items()):
        h.update(doc_id.encode())
        h.update(doc.path.read_bytes())
    return h.hexdigest()[:16]
