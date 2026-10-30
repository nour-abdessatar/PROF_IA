"""
reranker.py — step 9 of the RAG pipeline.

Scores each (query, chunk) pair with the BAAI/bge-reranker-v2-m3 cross-encoder,
drops chunks below RERANK_CUTOFF, keeps the best RERANK_TOP_K and reorders them
("long-context reorder") before they are handed to the prompt builder.

    retriever -> reranker -> LLM
"""

from __future__ import annotations

import argparse
import math
import time
from dataclasses import dataclass, field
from typing import Any, Optional, Protocol, runtime_checkable

import torch
from transformers import AutoModelForSequenceClassification, AutoTokenizer

from langsmith import traceable

# ─────────────────────────── CONFIG ────────────────────────────

RERANK_MODEL   = "BAAI/bge-reranker-v2-m3"
RERANK_TOP_K   = 3
RERANK_CUTOFF  = -5.0   # raw logit; chunks below it are dropped
MAX_LENGTH     = 512    # tokens per (query, chunk) pair

if torch.cuda.is_available():
    DEVICE = torch.device("cuda")
elif torch.backends.mps.is_available():
    DEVICE = torch.device("mps")
else:
    DEVICE = torch.device("cpu")


# ─────────────────────────── TYPES ─────────────────────────────

@runtime_checkable
class ChunkLike(Protocol):
    chunk_id : Any
    content  : str


@dataclass
class RankedChunk:
    """A chunk after reranking; the contract with the prompt builder."""
    chunk_id        : Any
    rerank_score    : float
    rerank_prob     : float
    position        : int
    content         : str
    payload         : dict  = field(default_factory=dict)
    # Fields copied from RetrievedChunk
    retriever_score : float = 0.0
    level           : str   = ""
    subject         : str   = ""
    lesson_number   : str   = ""
    lesson_title    : str   = ""
    has_formula     : bool  = False
    keywords        : list  = field(default_factory=list)
    above_cutoff    : bool  = True


# ─────────────────────── MODEL LOADING ─────────────────────────

class CrossEncoderModel:
    def __init__(self):
        print(f"[INFO] Loading reranker '{RERANK_MODEL}'  (device={DEVICE})")
        self.tokenizer = AutoTokenizer.from_pretrained(RERANK_MODEL)
        self.model     = AutoModelForSequenceClassification.from_pretrained(RERANK_MODEL)
        self.model.eval()
        self.model.to(DEVICE)
        print("[OK]  Reranker ready")

    def score_pairs(self, pairs: list[tuple[str, str]]) -> list[float]:
        encoded = self.tokenizer(
            pairs, padding=True, truncation=True,
            max_length=MAX_LENGTH, return_tensors="pt",
        )
        encoded = {k: v.to(DEVICE) for k, v in encoded.items()}
        with torch.no_grad():
            logits = self.model(**encoded).logits
        return logits.squeeze(-1).cpu().float().tolist()


# ──────────────────── LONG CONTEXT REORDER ─────────────────────

def long_context_reorder(chunks: list[RankedChunk]) -> list[RankedChunk]:
    """
    Put the best chunks at both ends of the list (positions 0 and -1).

    Input is sorted best-first. Even indices go left in order, odd indices go
    right in reverse, e.g. [1, 2, 3, 4, 5] -> [1, 3, 5, 4, 2].

    NOTE: the result is no longer sorted by score. Callers that assume
    best-first order (e.g. select_relevant_lessons in pipeline.py) must not
    rely on the order of this output.
    """
    if len(chunks) <= 2:
        return chunks
    left, right = [], []
    for i, chunk in enumerate(chunks):
        (left if i % 2 == 0 else right).append(chunk)
    reordered = left + right[::-1]
    for pos, c in enumerate(reordered):
        c.position = pos
    return reordered


# ─────────────────────────── RERANKER ──────────────────────────

class Reranker:
    def __init__(
        self,
        model_name : str   = RERANK_MODEL,   # unused: the model is always RERANK_MODEL
        top_k      : int   = RERANK_TOP_K,
        cutoff     : float = RERANK_CUTOFF,
    ):
        self.top_k  = top_k
        self.cutoff = cutoff
        self._model = CrossEncoderModel()

    @staticmethod
    def _sigmoid(x: float) -> float:
        return 1.0 / (1.0 + math.exp(-x))

    @staticmethod
    def _get_content(chunk: ChunkLike) -> str:
        """Read the content field, falling back to an empty string."""
        return (getattr(chunk, "content", "") or "").strip()

    @staticmethod
    def _copy_metadata(src: ChunkLike, dst: RankedChunk):
        """Copy the lesson metadata from a RetrievedChunk to a RankedChunk."""
        dst.retriever_score = float(getattr(src, "score",         0.0))
        dst.level           = str(getattr(src,   "level",         ""))
        dst.subject         = str(getattr(src,   "subject",       ""))
        dst.lesson_number   = str(getattr(src,   "lesson_number", ""))
        dst.lesson_title    = str(getattr(src,   "lesson_title",  ""))
        dst.has_formula     = bool(getattr(src,  "has_formula",   False))
        dst.keywords        = list(getattr(src,  "keywords",      []) or [])
        dst.payload         = dict(getattr(src,  "payload",       {}))

    @traceable(name="rerank", run_type="chain")
    def rerank(
        self,
        query  : str,
        chunks : list[ChunkLike],
        top_k  : Optional[int]   = None,
        cutoff : Optional[float] = None,
    ) -> list[RankedChunk]:
        k   = top_k  if top_k  is not None else self.top_k
        thr = cutoff if cutoff is not None else self.cutoff

        if not chunks:
            return []

        # Empty query: nothing to score, keep the first k chunks as they are.
        if not query.strip():
            return [
                RankedChunk(
                    chunk_id=c.chunk_id, rerank_score=0.0,
                    rerank_prob=0.5, position=i,
                    content=self._get_content(c),
                )
                for i, c in enumerate(chunks[:k])
            ]

        contents = [self._get_content(c) for c in chunks]
        pairs    = [(query, t) for t in contents]
        logits   = self._model.score_pairs(pairs)

        scored: list[RankedChunk] = []
        for i, (chunk, logit) in enumerate(zip(chunks, logits)):
            rc = RankedChunk(
                chunk_id     = chunk.chunk_id,
                rerank_score = float(logit),
                rerank_prob  = self._sigmoid(float(logit)),
                position     = i,
                content      = contents[i],
                above_cutoff = float(logit) >= thr,
            )
            self._copy_metadata(chunk, rc)
            scored.append(rc)

        scored.sort(key=lambda x: x.rerank_score, reverse=True)

        # If everything is below the cutoff, still keep the single best chunk.
        above = [c for c in scored if c.above_cutoff]
        if not above:
            print(f"[WARN] All chunks are below the cutoff ({thr:.1f}). "
                  f"Best score={scored[0].rerank_score:.2f}. Keeping it anyway.")
            scored[0].above_cutoff = False
            above = scored[:1]

        return long_context_reorder(above[:k])


# ──────────────────────── CLI REPORT ───────────────────────────

def print_results(results: list[RankedChunk], query: str, elapsed: float, cutoff: float):
    print("\n" + "=" * 70)
    print(f"  RERANKER — query: {query!r}")
    print(f"  {len(results)} chunks kept  |  {elapsed*1000:.0f} ms  |  cutoff={cutoff}")
    print("=" * 70)
    for r in results:
        flag = "✓" if r.above_cutoff else "⚠ below cutoff"
        print(
            f"\n  [pos={r.position}] score={r.rerank_score:+.3f}  "
            f"prob={r.rerank_prob:.3f}  {flag}\n"
            f"  chunk_id={r.chunk_id}  level={r.level}  subject={r.subject}\n"
            f"  lesson_number={r.lesson_number}  lesson_title={r.lesson_title}  "
            f"formula={r.has_formula}"
        )
        print(f"  {r.content[:180].replace(chr(10), ' ')!r}")
    print("=" * 70 + "\n")


# ─────────────────────────── CLI ───────────────────────────────

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="RAG pipeline — reranker")
    p.add_argument("--query",         required=True)
    p.add_argument("--collection",    default="tunisian_college")
    p.add_argument("--top-k",         type=int,   default=RERANK_TOP_K)
    p.add_argument("--cutoff",        type=float, default=RERANK_CUTOFF)
    p.add_argument("--mode",          default="local",
                   choices=["local", "server", "memory"])
    p.add_argument("--host",          default="localhost")
    p.add_argument("--port",          type=int, default=6333)
    # Filters (fields that exist on the chunks)
    p.add_argument("--level",         default=None, help="7eme | 8eme | 9eme")
    p.add_argument("--subject",       default=None, help="math | …")
    p.add_argument("--lesson-number", default=None, help="1 | 2 | …")
    p.add_argument("--lesson-title",  default=None, help="الأعداد الأولية | …")
    return p.parse_args()


def main():
    args = parse_args()

    from retriever import Retriever, SearchFilters

    filters = SearchFilters(
        level         = args.level,
        subject       = args.subject,
        lesson_number = args.lesson_number,
        lesson_title  = args.lesson_title,
    )
    any_filter = any(v is not None for v in [
        filters.level, filters.subject, filters.lesson_number, filters.lesson_title,
    ])

    retriever = Retriever(
        collection=args.collection, mode=args.mode,
        host=args.host, port=args.port, lazy_bm25=False,
    )

    retrieved = retriever.search(
        query   = args.query,
        top_k   = 10,
        filters = filters if any_filter else None,
    )
    print(f"[INFO] Retriever → {len(retrieved)} chunks")

    reranker = Reranker(top_k=args.top_k, cutoff=args.cutoff)

    t0      = time.perf_counter()
    ranked  = reranker.rerank(args.query, retrieved)
    elapsed = time.perf_counter() - t0

    print_results(ranked, args.query, elapsed, args.cutoff)


if __name__ == "__main__":
    main()