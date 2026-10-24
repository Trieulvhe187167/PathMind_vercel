"""Finding the passages that answer a question: company tree chunks and personal knowledge bases.

Scoring combines
* coverage: how much of the question's (idf-weighted) wording a passage contains - an absolute
  0..1 signal used to decide whether the tree can answer at all;
* BM25 for ranking among candidates;
* cosine similarity with embeddings when an embedding model is configured.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import json
import math
from pathlib import Path
import re
import unicodedata

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import KnowledgeChunk, KnowledgeDomain, KnowledgeNode, VerifiedDocument
from . import llm
from .knowledge_tree import load_vectors

STOP = set("""
a an the and or of to in on for with is are was were be been it this that these those what which who whom how why when
where do does did can could should would will i you he she we they my your our their me us them as at by from about into
than then there here not no yes if so but also just only very more most some any all each other such own same
là và của các những một có không được cho với trong trên này đó thì mà nào gì sao khi để từ ra vào đã đang sẽ rất cũng
như nếu bị bởi tôi bạn chúng ta họ anh chị em ở thế làm hay hoặc vì nên
的 了 是 在 和 有 我 你 他 她 它 们 这 那 吗 呢 吧 怎么 什么
""".split())


def _fold(text: str) -> str:
    """Lower-case and strip Vietnamese diacritics so 'bảo quản' matches 'bao quan'."""
    t = unicodedata.normalize("NFD", text.lower()).replace("đ", "d")
    return "".join(ch for ch in t if unicodedata.category(ch) != "Mn")


def _stem(w: str) -> str:
    """Very light English stemming so 'acids' matches 'acid' and 'storing' matches 'store'."""
    if not w.isascii() or len(w) <= 4:
        return w
    for suf, rep in (("ies", "y"), ("ing", ""), ("ed", ""), ("es", ""), ("s", "")):
        if w.endswith(suf) and len(w) - len(suf) >= 3:
            return w[: len(w) - len(suf)] + rep
    return w


def tokenize(text: str) -> list[str]:
    words = re.findall(r"\w+", text.lower())
    out = []
    for w in words:
        if w in STOP or len(w) < 2 and not w.isdigit():
            continue
        out.append(_stem(_fold(w)))
    # CJK has no spaces: add character bigrams so Chinese questions still match.
    cjk = re.findall(r"[一-鿿]+", text)
    for run in cjk:
        out += [run[i:i + 2] for i in range(len(run) - 1)] or [run]
    return out


@dataclass
class Doc:
    key: str
    text: str
    meta: dict = field(default_factory=dict)
    vector: list[float] | None = None


@dataclass
class Hit:
    doc: Doc
    score: float
    coverage: float
    bm25: float
    cosine: float = 0.0


def rank(question: str, docs: list[Doc], qvec: list[float] | None = None, limit: int = 6) -> list[Hit]:
    q = list(dict.fromkeys(tokenize(question)))
    if not docs or (not q and qvec is None):
        return []
    toks = [tokenize(d.text) for d in docs]
    n = len(docs)
    df: dict[str, int] = {}
    for t in toks:
        for w in set(t):
            df[w] = df.get(w, 0) + 1
    # Words that appear nowhere get the weight of a rare word (not more), so one unknown word
    # does not make every passage look irrelevant.
    idf = {w: math.log(1 + (n - max(df.get(w, 0), 1) + 0.5) / (max(df.get(w, 0), 1) + 0.5)) for w in q}
    total_idf = sum(idf.values()) or 1.0
    avgdl = sum(len(t) for t in toks) / n or 1.0
    k1, b = 1.4, 0.75
    hits: list[Hit] = []
    for d, t in zip(docs, toks):
        tf: dict[str, int] = {}
        for w in t:
            if w in idf:
                tf[w] = tf.get(w, 0) + 1
        bm = sum(idf[w] * tf[w] * (k1 + 1) / (tf[w] + k1 * (1 - b + b * len(t) / avgdl)) for w in tf)
        cov = sum(idf[w] for w in tf) / total_idf if q else 0.0
        cos = llm.cosine(qvec, d.vector) if (qvec is not None and d.vector) else 0.0
        hits.append(Hit(d, 0.0, cov, bm, cos))
    max_bm = max((h.bm25 for h in hits), default=0) or 1.0
    use_vec = qvec is not None and any(h.cosine for h in hits)
    for h in hits:
        bmn = h.bm25 / max_bm
        h.score = (0.45 * h.coverage + 0.2 * bmn + 0.35 * max(h.cosine, 0)) if use_vec else (0.7 * h.coverage + 0.3 * bmn)
    hits.sort(key=lambda h: h.score, reverse=True)
    return [h for h in hits[:limit] if h.score > 0]


def relevance(h: Hit) -> float:
    """Absolute 'does this passage answer it' signal for the gap / strict-policy decision."""
    return max(h.coverage, h.cosine if h.cosine > 0.75 else 0.0)


def _question_vector(db: Session, question: str, user) -> list[float] | None:
    try:
        res = llm.embed(db, [question], user=user, purpose="retrieval")
    except llm.LLMError:
        return None
    return res[0][0] if res else None


# ── The company tree ──────────────────────────────────────────────────────────


@dataclass
class TreeResult:
    hits: list[Hit]
    domain: KnowledgeDomain | None  # the domain the question seems to be about
    best: float  # relevance of the best hit


def search_tree(db: Session, data_dir: Path, question: str, *, user=None, limit: int = 6,
                domain_ids: list[str] | None = None) -> TreeResult:
    stmt = (select(KnowledgeChunk, VerifiedDocument, KnowledgeDomain)
            .join(VerifiedDocument, VerifiedDocument.id == KnowledgeChunk.document_id)
            .join(KnowledgeDomain, KnowledgeDomain.id == VerifiedDocument.domain_id)
            .where(KnowledgeChunk.is_active.is_(True), VerifiedDocument.status == "verified",
                   KnowledgeDomain.status == "active"))
    if domain_ids:
        stmt = stmt.where(KnowledgeDomain.id.in_(domain_ids))
    rows = db.execute(stmt).all()
    nodes = {n.id: n for n in db.scalars(select(KnowledgeNode).where(KnowledgeNode.status == "published"))}
    qvec = None
    if rows and any(c.embedding_model for c, _, _ in rows):
        qvec = _question_vector(db, question, user)
    vec_cache: dict[str, dict] = {}
    docs: list[Doc] = []
    for chunk, doc, dom in rows:
        if qvec is not None and chunk.embedding_model:
            vecs = vec_cache.setdefault(doc.id, load_vectors(data_dir, doc.id))
            vector = vecs.get(chunk.id)
        else:
            vector = None
        node = nodes.get(chunk.node_id) if chunk.node_id else None
        # The node's title and summary help route questions phrased differently from the document.
        text = chunk.text + (f"\n{node.title}. {node.summary}" if node else "")
        docs.append(Doc(chunk.id, text, {"chunk": chunk, "document": doc, "domain": dom, "node": node}, vector))
    hits = rank(question, docs, qvec, limit)
    best = relevance(hits[0]) if hits else 0.0
    domain = hits[0].doc.meta["domain"] if hits else None
    if domain is None or best < 0.2:
        domain = route_domain(db, question) or domain
    return TreeResult(hits, domain, best)


def route_domain(db: Session, question: str) -> KnowledgeDomain | None:
    """Which domain a question belongs to, from domain and node names/summaries (even without documents)."""
    domains = {d.id: d for d in db.scalars(select(KnowledgeDomain).where(KnowledgeDomain.status == "active"))}
    if not domains:
        return None
    items = [Doc(d.id, f"{d.name} {d.description}", {"domain": d}) for d in domains.values()]
    for n in db.scalars(select(KnowledgeNode).where(KnowledgeNode.status == "published")):
        if n.domain_id in domains:
            items.append(Doc(n.id, f"{n.title} {n.summary}", {"domain": domains[n.domain_id]}))
    hits = rank(question, items, None, 3)
    if hits and hits[0].coverage >= 0.3:
        return hits[0].doc.meta["domain"]
    return None


# ── Personal knowledge bases ──────────────────────────────────────────────────


def kb_index_path(data_dir: Path, kb_id: str) -> Path:
    return data_dir / "kb" / kb_id / "index.json"


def load_kb_index(data_dir: Path, kb_id: str) -> list[dict]:
    p = kb_index_path(data_dir, kb_id)
    if not p.exists():
        return []
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []


def save_kb_index(data_dir: Path, kb_id: str, items: list[dict]) -> None:
    p = kb_index_path(data_dir, kb_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(items, ensure_ascii=False), encoding="utf-8")
    tmp.replace(p)


def search_kbs(db: Session, data_dir: Path, question: str, kbs: list, *, user=None, limit: int = 4) -> list[Hit]:
    docs: list[Doc] = []
    for kb in kbs:
        for item in load_kb_index(data_dir, kb.id):
            docs.append(Doc(f"{kb.id}:{item['doc_id']}:{item['seq']}", item["text"],
                            {"kb": kb, "item": item}, item.get("vector")))
    if not docs:
        return []
    qvec = _question_vector(db, question, user) if any(d.vector for d in docs) else None
    return rank(question, docs, qvec, limit)
