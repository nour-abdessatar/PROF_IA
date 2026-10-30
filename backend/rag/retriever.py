"""
retriever.py — step 8 of the RAG pipeline.

Hybrid search: dense (multilingual-e5-base + Qdrant cosine) and sparse (local BM25)
results are merged with Reciprocal Rank Fusion (RRF), optionally filtered on chunk
metadata (level, subject, lesson_number, lesson_title, has_formula).

    embedding -> vector_store -> retriever -> reranker -> LLM

Usage:
    from retriever import Retriever, SearchFilters

    retriever = Retriever(collection="tunisian_college", mode="local")
    results   = retriever.search("كيف نحسب قوة عدد", top_k=5,
                                 filters=SearchFilters(level="7eme", subject="math"))

CLI:
    python retriever.py --query "ما هو تعريف القوة" --top-k 5 --level 7eme
"""

from __future__ import annotations

import argparse
import math
import re
import time
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import numpy as np
import torch
from qdrant_client import QdrantClient
from qdrant_client.http import models as qmodels
from sentence_transformers import SentenceTransformer

from langsmith import traceable

# ─────────────────────────── CONFIG ────────────────────────────

DEFAULT_COLLECTION  = "tunisian_college"
LOCAL_DB_PATH       = "output/qdrant_local"

EMBEDDING_MODEL     = "intfloat/multilingual-e5-base"
E5_QUERY_PREFIX     = "query: "
MAX_LENGTH          = 512

DENSE_WEIGHT        = 0.7
SPARSE_WEIGHT       = 0.3
RRF_K               = 6

DENSE_CANDIDATES    = 4
SPARSE_CANDIDATES   = 4

# Chunk fields indexed by BM25
BM25_TEXT_FIELDS    = ["content", "lesson_title"]
BM25_K1             = 1.5
BM25_B              = 0.75

if torch.cuda.is_available():
    DEVICE = torch.device("cuda")
elif torch.backends.mps.is_available():
    DEVICE = torch.device("mps")
else:
    DEVICE = torch.device("cpu")


# ─────────────────────────── TYPES ─────────────────────────────

@dataclass
class SearchFilters:
    """Optional metadata filters. None means no filter on that field."""
    level        : Optional[str]  = None   # "7eme" | "8eme" | "9eme"
    subject      : Optional[str]  = None   # "math" | …
    lesson_number: Optional[str]  = None   # "1" | "2" | …
    lesson_title : Optional[str]  = None   # "الأعداد الأولية" | …
    has_formula  : Optional[bool] = None   # True -> only chunks with formulas
    custom       : dict           = field(default_factory=dict)


@dataclass
class RetrievedChunk:
    """A chunk returned by the retriever (contract with the reranker / LLM)."""
    chunk_id      : str | int
    score         : float
    dense_rank    : int | None
    sparse_rank   : int | None
    # Content
    content       : str
    # Lesson metadata (used in the LLM prompt)
    level         : str  = ""
    subject       : str  = ""
    lesson_number : str  = ""
    lesson_title  : str  = ""
    has_formula   : bool = False
    keywords      : list = field(default_factory=list)
    # Full payload (every stored field)
    payload       : dict = field(default_factory=dict)


# ────────────────────────── BM25 ───────────────────────────────

def _tokenize(text: str) -> list[str]:
    text = text.lower()
    return re.findall(r"[\w\u0600-\u06FF]+", text)


class BM25Index:
    def __init__(self, docs: list[dict], k1: float = BM25_K1, b: float = BM25_B):
        self.k1  = k1
        self.b   = b
        self.ids = [d.get("chunk_id") for d in docs]

        corpus = []
        for d in docs:
            parts = [str(d.get(fld) or "") for fld in BM25_TEXT_FIELDS]
            corpus.append(_tokenize(" ".join(parts)))

        N      = len(corpus)
        avg_dl = sum(len(c) for c in corpus) / max(N, 1)
        df: dict[str, int] = defaultdict(int)
        for doc_tokens in corpus:
            for t in set(doc_tokens):
                df[t] += 1

        self.idf: dict[str, float] = {
            t: math.log((N - df[t] + 0.5) / (df[t] + 0.5) + 1)
            for t in df
        }
        self.corpus = corpus
        self.avg_dl = avg_dl
        self.dl     = [len(c) for c in corpus]

    def score(self, query_tokens: list[str], doc_idx: int) -> float:
        doc    = self.corpus[doc_idx]
        dl     = self.dl[doc_idx]
        tf_map: dict[str, int] = defaultdict(int)
        for t in doc:
            tf_map[t] += 1
        s = 0.0
        for t in query_tokens:
            if t not in self.idf:
                continue
            tf  = tf_map.get(t, 0)
            num = tf * (self.k1 + 1)
            den = tf + self.k1 * (1 - self.b + self.b * dl / max(self.avg_dl, 1))
            s  += self.idf[t] * num / den
        return s

    def search(self, query: str, top_k: int) -> list[tuple[str | int, float]]:
        q_tokens = _tokenize(query)
        if not q_tokens:
            return []
        scores = [
            (self.ids[i], self.score(q_tokens, i))
            for i in range(len(self.ids))
        ]
        scores.sort(key=lambda x: x[1], reverse=True)
        return scores[:top_k]


# ─────────────────────── EMBEDDING ─────────────────────────────

class QueryEncoder:
    def __init__(self):
        print(f"[INFO] Loading encoder '{EMBEDDING_MODEL}'  (device={DEVICE})")
        self.model = SentenceTransformer(EMBEDDING_MODEL, device=str(DEVICE))
        print("[OK]  Encoder ready")

    @traceable(name="encode_query", run_type="embedding")
    def encode(self, query: str) -> np.ndarray:
        prefixed = query if query.startswith(E5_QUERY_PREFIX) else E5_QUERY_PREFIX + query
        vec = self.model.encode(prefixed, normalize_embeddings=True)
        return vec.astype(np.float32)


# ─────────────────────── QDRANT CLIENT ─────────────────────────

def build_client(mode: str, host: str, port: int) -> QdrantClient:
    if mode == "local":
        Path(LOCAL_DB_PATH).mkdir(parents=True, exist_ok=True)
        return QdrantClient(path=LOCAL_DB_PATH)
    if mode == "server":
        return QdrantClient(host=host, port=port)
    if mode == "memory":
        return QdrantClient(":memory:")
    raise ValueError(f"Unknown mode: {mode!r}")


# ─────────────────────── QDRANT FILTER ─────────────────────────

def build_qdrant_filter(f: SearchFilters) -> Optional[qmodels.Filter]:
    conditions = []

    def _eq(key: str, val) -> qmodels.FieldCondition:
        return qmodels.FieldCondition(key=key, match=qmodels.MatchValue(value=val))

    if f.level         is not None: conditions.append(_eq("level",         f.level))
    if f.subject       is not None: conditions.append(_eq("subject",       f.subject))
    if f.lesson_number is not None: conditions.append(_eq("lesson_number", f.lesson_number))
    if f.lesson_title  is not None: conditions.append(_eq("lesson_title",  f.lesson_title))
    if f.has_formula   is not None: conditions.append(_eq("has_formula",   f.has_formula))

    for key, val in (f.custom or {}).items():
        conditions.append(_eq(key, val))

    return qmodels.Filter(must=conditions) if conditions else None


# ─────────────────────── RRF FUSION ────────────────────────────

def reciprocal_rank_fusion(
    dense_hits  : list[tuple[str | int, float]],
    sparse_hits : list[tuple[str | int, float]],
    k           : int   = RRF_K,
    w_dense     : float = DENSE_WEIGHT,
    w_sparse    : float = SPARSE_WEIGHT,
) -> list[tuple[str | int, float]]:
    dense_rank  = {cid: r + 1 for r, (cid, _) in enumerate(dense_hits)}
    sparse_rank = {cid: r + 1 for r, (cid, _) in enumerate(sparse_hits)}
    all_ids     = set(dense_rank) | set(sparse_rank)
    n_d, n_s    = len(dense_hits) + 1, len(sparse_hits) + 1

    fused = [
        (cid, w_dense / (k + dense_rank.get(cid, n_d))
             + w_sparse / (k + sparse_rank.get(cid, n_s)))
        for cid in all_ids
    ]
    fused.sort(key=lambda x: x[1], reverse=True)
    return fused


# ─────────────────────── RETRIEVER ─────────────────────────────

class Retriever:
    def __init__(
        self,
        collection : str  = DEFAULT_COLLECTION,
        mode       : str  = "local",
        host       : str  = "localhost",
        port       : int  = 6333,
        lazy_bm25  : bool = True,
    ):
        self.collection = collection
        self.client     = build_client(mode, host, port)
        self.encoder    = QueryEncoder()
        self._bm25      : BM25Index | None = None
        self._lazy_bm25 = lazy_bm25
        self._all_docs  : list[dict] | None = None

        if not lazy_bm25:
            self._build_bm25_index()

    def _scroll_all_payloads(self) -> list[dict]:
        print(f"[INFO] Loading BM25 payloads (collection='{self.collection}')…")
        docs, offset = [], None
        while True:
            results, next_offset = self.client.scroll(
                collection_name=self.collection, limit=500,
                offset=offset, with_vectors=False, with_payload=True,
            )
            for pt in results:
                if pt.payload:
                    docs.append(pt.payload)
            if next_offset is None:
                break
            offset = next_offset
        print(f"[OK]  {len(docs)} payloads loaded")
        return docs

    def _build_bm25_index(self):
        if self._all_docs is None:
            self._all_docs = self._scroll_all_payloads()
        self._bm25 = BM25Index(self._all_docs)
        print(f"[OK]  BM25 index built ({len(self._all_docs)} docs)")

    @traceable(name="dense_search", run_type="retriever")
    def _dense_search(self, query_vec, top_k, qfilter):
        # TODO: QdrantClient.search() was removed in qdrant-client >= 1.16.
        # Either pin qdrant-client<1.16 or migrate to client.query_points().
        hits = self.client.search(
            collection_name=self.collection, query_vector=query_vec.tolist(),
            limit=top_k, query_filter=qfilter, with_payload=True,
        )
        ranked, payloads = [], {}
        for h in hits:
            cid = h.payload.get("chunk_id", h.id) if h.payload else h.id
            ranked.append((cid, float(h.score)))
            if h.payload:
                payloads[cid] = h.payload
        return ranked, payloads

    @traceable(name="sparse_search", run_type="retriever")
    def _sparse_search(self, query, top_k, qfilter):
        if self._bm25 is None:
            self._build_bm25_index()

        raw = self._bm25.search(query, top_k=top_k * 3)

        if qfilter and self._all_docs is not None:
            pid_map = {d.get("chunk_id"): d for d in self._all_docs}
            raw = [
                (cid, sc) for cid, sc in raw
                if _payload_matches_filter(pid_map.get(cid, {}), qfilter)
            ]

        ranked   = raw[:top_k]
        payloads = {}
        if self._all_docs:
            pid_map = {d.get("chunk_id"): d for d in self._all_docs}
            for cid, _ in ranked:
                if cid in pid_map:
                    payloads[cid] = pid_map[cid]
        return ranked, payloads

    @traceable(name="hybrid_retrieval", run_type="retriever", metadata={"method": "RRF"})
    def search(
        self,
        query            : str,
        top_k            : int                    = 10,
        filters          : Optional[SearchFilters] = None,
        dense_candidates : int                    = DENSE_CANDIDATES,
        sparse_candidates: int                    = SPARSE_CANDIDATES,
        rrf_k            : int                    = RRF_K,
        dense_weight     : float                  = DENSE_WEIGHT,
        sparse_weight    : float                  = SPARSE_WEIGHT,
    ) -> list[RetrievedChunk]:
        if not query.strip():
            return []

        qfilter = build_qdrant_filter(filters) if filters else None

        query_vec                    = self.encoder.encode(query)
        dense_hits,  dense_payloads  = self._dense_search(query_vec, dense_candidates, qfilter)
        sparse_hits, sparse_payloads = self._sparse_search(query, sparse_candidates, qfilter)

        fused = reciprocal_rank_fusion(
            dense_hits, sparse_hits,
            k=rrf_k, w_dense=dense_weight, w_sparse=sparse_weight,
        )

        dense_rank_map  = {cid: r + 1 for r, (cid, _) in enumerate(dense_hits)}
        sparse_rank_map = {cid: r + 1 for r, (cid, _) in enumerate(sparse_hits)}
        all_payloads    = {**sparse_payloads, **dense_payloads}

        results: list[RetrievedChunk] = []
        for cid, rrf_score in fused[:top_k]:
            p = all_payloads.get(cid, {})
            results.append(RetrievedChunk(
                chunk_id      = cid,
                score         = rrf_score,
                dense_rank    = dense_rank_map.get(cid),
                sparse_rank   = sparse_rank_map.get(cid),
                content       = p.get("content", ""),
                level         = p.get("level", ""),
                subject       = p.get("subject", ""),
                lesson_number = p.get("lesson_number", ""),
                lesson_title  = p.get("lesson_title", ""),
                has_formula   = p.get("has_formula", False),
                keywords      = p.get("keywords", []) or [],
                payload       = p,
            ))

        return results


# ─────────────────── PAYLOAD FILTER HELPER ─────────────────────

def _payload_matches_filter(doc: dict, qfilter: qmodels.Filter) -> bool:
    for cond in (qfilter.must or []):
        if not isinstance(cond, qmodels.FieldCondition):
            continue
        expected = cond.match.value if hasattr(cond.match, "value") else None
        if doc.get(cond.key) != expected:
            return False
    return True


# ──────────────────────── CLI REPORT ───────────────────────────

def print_results(results: list[RetrievedChunk], query: str, elapsed: float):
    print("\n" + "=" * 70)
    print(f"  RESULTS — query: {query!r}")
    print(f"  {len(results)} chunks  |  {elapsed*1000:.0f} ms")
    print("=" * 70)
    for i, r in enumerate(results, 1):
        d = f"D#{r.dense_rank}"  if r.dense_rank  else "D:—"
        s = f"S#{r.sparse_rank}" if r.sparse_rank else "S:—"
        print(
            f"\n  [{i:2d}] score={r.score:.4f}  {d}  {s}  chunk_id={r.chunk_id}\n"
            f"       level={r.level}  subject={r.subject}\n"
            f"       lesson_number={r.lesson_number}  lesson_title={r.lesson_title}\n"
            f"       formula={r.has_formula}"
        )
        preview = (r.content or "")[:160].replace("\n", " ")
        print(f"       {preview!r}")
    print("=" * 70 + "\n")


# ─────────────────────────── CLI ───────────────────────────────

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="RAG pipeline — retriever")
    p.add_argument("--query",         required=True,              help="Search query")
    p.add_argument("--collection",    default=DEFAULT_COLLECTION, help="Qdrant collection")
    p.add_argument("--top-k",         type=int, default=5,        help="Number of results")
    p.add_argument("--mode",          default="local",
                   choices=["local", "server", "memory"])
    p.add_argument("--host",          default="localhost")
    p.add_argument("--port",          type=int, default=6333)
    # Filters (fields that exist on the chunks)
    p.add_argument("--level",         default=None, help="7eme | 8eme | 9eme")
    p.add_argument("--subject",       default=None, help="math | …")
    p.add_argument("--lesson-number", default=None, help="1 | 2 | …")
    p.add_argument("--lesson-title",  default=None, help="الأعداد الأولية | …")
    p.add_argument("--formula",       action="store_true", default=None)
    # Tuning
    p.add_argument("--dense-weight",  type=float, default=DENSE_WEIGHT)
    p.add_argument("--sparse-weight", type=float, default=SPARSE_WEIGHT)
    p.add_argument("--rrf-k",         type=int,   default=RRF_K)
    return p.parse_args()


def main():
    args = parse_args()

    filters = SearchFilters(
        level         = args.level,
        subject       = args.subject,
        lesson_number = args.lesson_number,
        lesson_title  = args.lesson_title,
        has_formula   = args.formula if args.formula else None,
    )
    any_filter = any(v is not None for v in [
        filters.level, filters.subject, filters.lesson_number,
        filters.lesson_title, filters.has_formula,
    ])

    retriever = Retriever(
        collection=args.collection, mode=args.mode,
        host=args.host, port=args.port, lazy_bm25=False,
    )

    t0      = time.perf_counter()
    results = retriever.search(
        query         = args.query,
        top_k         = args.top_k,
        filters       = filters if any_filter else None,
        dense_weight  = args.dense_weight,
        sparse_weight = args.sparse_weight,
        rrf_k         = args.rrf_k,
    )
    elapsed = time.perf_counter() - t0

    print_results(results, args.query, elapsed)


if __name__ == "__main__":
    main()