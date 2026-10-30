"""
pipeline.py — orchestrator of the Prof IA RAG pipeline.

Ingestion (run once per course JSON):
    1. json_process -> output/embedded_chunk.json   (clean + chunk)
    2. embedding    -> output/chunks_embedded.json  (multilingual-e5-base)
    3. vector_store -> Qdrant collection

Query (called at chat time):
    retriever -> reranker -> lesson dict(s) -> llm -> answer / token stream

CLI:
    python pipeline.py ingest --input data/math.json [--skip-process | --skip-embed]
    python pipeline.py query --question "ما هو العدد الأولي" [--stream]

Python:
    from pipeline import query_rag_traced, query_rag_stream
    for token in query_rag_stream("ما هو العدد الأولي"):
        print(token, end="", flush=True)
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path
from typing import Generator

from langsmith import traceable

# ─────────────────────────── CONFIG ────────────────────────────

OUTPUT_DIR          = "output"
PROCESSED_JSON      = f"{OUTPUT_DIR}/embedded_chunk.json"
EMBEDDED_JSON       = f"{OUTPUT_DIR}/chunks_embedded.json"
QDRANT_COLLECTION   = "tunisian_college"
QDRANT_LOCAL_PATH   = f"{OUTPUT_DIR}/qdrant_local"

# ─────────────────────── RETRIEVER SINGLETON ───────────────────
# The embedding model is slow to load on CPU (~140 s), so one instance is kept
# for the lifetime of the process and created on first use (or at warm-up).

_retriever_instance = None

def get_retriever(collection: str = QDRANT_COLLECTION):
    global _retriever_instance
    if _retriever_instance is None:
        from retriever import Retriever
        print("[pipeline] Loading retriever (first call — model will be cached after this)...")
        _retriever_instance = Retriever(collection=collection, mode="local")
        print("[pipeline] Retriever ready.")
    return _retriever_instance


# ─────────────────────── RERANKER SINGLETON ────────────────────
# Cross-encoder is expensive to load — keep one instance alive.

_reranker_instance = None

def get_reranker():
    global _reranker_instance
    if _reranker_instance is None:
        from reranker import Reranker
        print("[pipeline] Loading reranker (first call — model will be cached after this)...")
        _reranker_instance = Reranker()
        print("[pipeline] Reranker ready.")
    return _reranker_instance


# ───────────────────────── HELPERS ─────────────────────────────

def _sep(title: str):
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}")


def _require_file(path: str, step: str):
    if not Path(path).exists():
        print(f"\n[ERROR] Required file missing for {step}: {path}")
        sys.exit(1)


# ─────────────────── LESSON INTENT DETECTION ───────────────────

def extract_lesson_number(question: str) -> str | None:
    """
    Detect if the student is asking for a specific lesson number.
    Handles:
      - Arabic digits:   "الدرس 2" / "درس 2"
      - Arabic ordinals: "الدرس الثاني" / "درس الثالث"
      - French:          "leçon 2" / "lecon 2"

    Returns the lesson number as a string ("1", "2"...) or None.
    """
    arabic_ordinals = {
        "الأول"  : "1",  "الأولى"  : "1",
        "الثاني" : "2",  "الثانية" : "2",
        "الثالث" : "3",  "الثالثة" : "3",
        "الرابع" : "4",  "الرابعة" : "4",
        "الخامس" : "5",  "الخامسة" : "5",
        "السادس" : "6",  "السادسة" : "6",
        "السابع" : "7",  "السابعة" : "7",
        "الثامن" : "8",  "الثامنة" : "8",
        "التاسع" : "9",  "التاسعة" : "9",
        "العاشر" : "10", "العاشرة" : "10",
    }

    # "الدرس 2" / "درس 2" / "leçon 2" / "lecon 2"
    digit_match = re.search(
        r"(?:الدرس|درس|leçon|le[cç]on)\s*(\d+)",
        question, re.IGNORECASE
    )
    if digit_match:
        return digit_match.group(1)

    # "الدرس الثاني" / "درس الثالث"
    for word, num in arabic_ordinals.items():
        if re.search(rf"(?:الدرس|درس)\s+{word}", question):
            return num

    return None


# ─────────────────── MULTI-LESSON SELECTION ────────────────────
# These helpers read chunk attributes with getattr() fallbacks because the exact
# shape returned by reranker.rerank() is not owned by this file.

def _chunk_score(chunk) -> float:
    """
    Read a chunk's rerank score from whichever attribute is populated.

    `rerank_prob` (sigmoid of the logit, always in [0, 1]) is checked before
    `rerank_score`: the raw logit is negative in this reranker's normal range
    (RERANK_CUTOFF = -5.0), which would break the ratio threshold used by
    select_relevant_lessons(). Never raises; returns 0.0 if nothing is usable.
    """
    for attr in ("rerank_prob", "rerank_score", "score", "relevance_score"):
        val = getattr(chunk, attr, None)
        if isinstance(val, (int, float)):
            return float(val)
    return 0.0


def _lesson_identity(chunk) -> tuple[str, str]:
    """
    Return the (lesson_number, lesson_title) pair used to tell whether two
    chunks belong to the same lesson. Never raises on missing attributes.
    """
    number = str(getattr(chunk, "lesson_number", "") or "").strip()
    title  = str(getattr(chunk, "lesson_title", "") or "").strip()
    return (number, title)


def _chunk_to_lesson_dict(chunk) -> dict:
    """
    Build the standard lesson dict from a single chunk. Used by both the
    single-lesson path and the multi-lesson selection.

    `keywords` is a list of terminology/phrasing rules the LLM must follow for
    this lesson (authored in the source lesson JSON). It is [] when the lesson
    has none, or when the chunk type does not carry the attribute (check
    RetrievedChunk and RankedChunk if keywords stop reaching the prompt).
    """
    return {
        "lesson_title" : getattr(chunk, "lesson_title", "") or "",
        "lesson_number": getattr(chunk, "lesson_number", "") or "",
        "level"        : getattr(chunk, "level", "") or "",
        "subject"      : getattr(chunk, "subject", "") or "",
        "content"      : getattr(chunk, "content", "") or "",
        "keywords"     : getattr(chunk, "keywords", None) or [],
    }


def select_relevant_lessons(
    ranked                : list,
    max_lessons            : int   = 3,
    score_ratio_threshold  : float = 0.6,
    min_prob               : float | None = None,
) -> list[dict]:
    """
    Select every distinct relevant lesson from a reranked chunk list instead of
    keeping only the top chunk.

    Parameters
    ----------
    ranked                : reranker output. NOTE: the loop below assumes it is
                            sorted best-first, but Reranker.rerank() returns
                            chunks after long_context_reorder(), which is NOT
                            sorted by score (see the TODO in the loop).
    max_lessons           : hard cap on the number of distinct lessons returned.
    score_ratio_threshold : a lesson's best chunk must score at least this
                            fraction of the best score to be included. The
                            threshold is relative so it adapts to the score
                            scale; it relies on _chunk_score() being a bounded,
                            non-negative probability.
    min_prob              : optional absolute floor on _chunk_score(). A chunk
                            is kept if it clears EITHER this floor or the ratio
                            threshold (whichever is easier). Useful when two
                            legitimate topics score very differently. None
                            (default) disables it.

    Returns
    -------
    1..max_lessons lesson dicts, each distinct lesson once (via its
    best-scoring chunk). Empty only if `ranked` is empty.
    """
    if not ranked:
        return []

    top_score = _chunk_score(ranked[0])

    # Defensive fallback for a degenerate top score of exactly 0 (e.g. a chunk
    # without any usable score attribute): keep just the first lesson.
    if top_score == 0:
        return [_chunk_to_lesson_dict(ranked[0])]

    ratio_floor = top_score * score_ratio_threshold
    # The effective floor is the lower (easier to pass) of the two thresholds.
    # With min_prob=None it reduces to the ratio floor.
    effective_floor = ratio_floor if min_prob is None else min(ratio_floor, min_prob)

    seen_identities: set[tuple[str, str]] = set()
    selected: list[dict] = []

    for chunk in ranked:
        if len(selected) >= max_lessons:
            break

        ident = _lesson_identity(chunk)
        if ident in seen_identities:
            continue  # already have this lesson's best chunk

        score = _chunk_score(chunk)
        if score < effective_floor:
            # TODO: this `break` assumes best-first order, but `ranked` comes
            # from long_context_reorder() (e.g. scores [1st, 3rd, 5th, 4th, 2nd]).
            # A low-scoring chunk in the middle can stop the scan before a
            # relevant chunk at the end is seen. `continue` (or sorting by
            # _chunk_score first) would be safer; left as is on purpose so
            # behaviour is unchanged.
            break

        seen_identities.add(ident)
        selected.append(_chunk_to_lesson_dict(chunk))

    # Safety net: cannot normally happen (the first chunk always passes), but
    # guarantees a non-empty result for non-empty input.
    if not selected:
        selected = [_chunk_to_lesson_dict(ranked[0])]

    return selected


# ─────────────────────── RETRIEVE & RERANK ─────────────────────

@traceable(name="retrieve_and_rerank", run_type="chain")
def _retrieve_and_rerank(
    question      : str,
    collection    : str = QDRANT_COLLECTION,
    top_k_retrieve: int = 5,
    filters       : dict | None = None,
    max_lessons   : int = 3,
    min_prob      : float | None = 0.005,
) -> tuple[list, dict, list[dict]]:
    """
    Retrieval + reranking shared by the traced and streaming query functions.

    Returns
    -------
    (ranked_chunks, lesson, lessons)
      - ranked_chunks : the full reranked chunk list.
      - lesson        : best-matching lesson dict (== lessons[0]), kept for
                        backward compatibility.
      - lessons       : all distinct relevant lesson dicts (1..max_lessons).

    The candidate pool of retriever.search() is widened to `top_k_retrieve`
    (floor of 4); retriever.py's own 4/4 defaults are unchanged.

    `min_prob` (default 0.005) is the absolute floor passed to
    select_relevant_lessons(). It was tuned on one real two-topic trace: the
    second lesson had rerank_prob≈0.0095 against a top score of ≈0.153, and
    0.02 was too strict while 0.005 kept both. It is close to the noise floor
    of the cross-encoder, so it may admit marginal lessons: raise it, or pass
    min_prob=None to rely on the ratio threshold alone, if irrelevant lessons
    show up in real traffic.
    """
    from retriever import SearchFilters

    # ── Auto-detect lesson number from student query ───────────
    detected = extract_lesson_number(question)
    if detected:
        print(f"  [pipeline] Lesson intent detected → lesson_number={detected}")
        filters = filters or {}
        filters["lesson_number"] = detected
    # ──────────────────────────────────────────────────────────

    search_filters = None
    if filters:
        search_filters = SearchFilters(**{
            k: v for k, v in filters.items()
            if k in SearchFilters.__dataclass_fields__
        })

    retriever = get_retriever(collection)
    # retriever.search()'s `top_k` is applied after dense+sparse fusion; the real
    # candidate ceiling is dense_candidates / sparse_candidates (4/4 by default).
    # Scale both to top_k_retrieve (minimum 4) so a relevant second topic is not
    # crowded out by a dominant one. The override is local to this call site.
    retrieved = retriever.search(
        query             = question,
        top_k             = top_k_retrieve,
        filters           = search_filters,
        dense_candidates  = max(4, top_k_retrieve),
        sparse_candidates = max(4, top_k_retrieve),
    )
    print(f"  [pipeline] Retrieved {len(retrieved)} chunks")

    if not retrieved:
        return [], {}, []

    reranker = get_reranker()
    ranked   = reranker.rerank(query=question, chunks=retrieved)
    print(f"  [pipeline] Reranked → {len(ranked)} chunks kept")

    # Select ALL distinct relevant lessons, not only ranked[0].
    lessons = select_relevant_lessons(ranked, max_lessons=max_lessons, min_prob=min_prob)
    lesson  = lessons[0] if lessons else {}

    if len(lessons) > 1:
        titles = " | ".join(f"{l['lesson_title']} (#{l['lesson_number']})" for l in lessons)
        print(f"  [pipeline] {len(lessons)} distinct lessons selected: {titles}")
    else:
        print(f"  [pipeline] Lesson: {lesson.get('lesson_title')} (level={lesson.get('level')})")

    return ranked, lesson, lessons


# ══════════════════════════════════════════════════════════════
#  STAGE 1 — json_process → embedded_chunk.json
# ══════════════════════════════════════════════════════════════

def run_json_process(input_path: str, max_tokens: int = 512) -> list[dict]:
    _sep("STAGE 1 — JSON Process (clean + chunk)")

    src = Path(input_path)
    if not src.exists():
        print(f"[ERROR] Input file not found: {input_path}")
        sys.exit(1)

    from json_process import process_record
    from lesson_schema import load_and_validate_lessons, LessonValidationError

    # Validated load: reports every malformed lesson at ingestion time (wrong
    # subject/level, empty content, duplicate lesson_number, unknown fields)
    # instead of letting it reach Qdrant. See lesson_schema.py.
    try:
        data: list[dict] = load_and_validate_lessons(src)
    except (FileNotFoundError, LessonValidationError) as e:
        print(f"[ERROR] {e}")
        sys.exit(1)

    print(f"  Loaded {len(data)} records from {src}")

    counter = [0]
    output_records: list[dict] = []

    for idx, record in enumerate(data):
        try:
            chunks = process_record(record, counter, max_tokens=max_tokens)
            output_records.extend(chunks)
        except Exception as exc:
            print(f"  [WARN] Skipping record {idx}: {exc}")

    print(f"  Processed: {len(data)} records → {len(output_records)} chunks")

    Path(PROCESSED_JSON).parent.mkdir(parents=True, exist_ok=True)
    with open(PROCESSED_JSON, "w", encoding="utf-8") as f:
        json.dump(output_records, f, ensure_ascii=False, indent=2)

    print(f"  Saved → {PROCESSED_JSON}")
    return output_records


# ══════════════════════════════════════════════════════════════
#  STAGE 2 — embedding → chunks_embedded.json
# ══════════════════════════════════════════════════════════════

def run_embedding() -> list[dict]:
    _sep("STAGE 2 — Embedding (multilingual-e5-base)")
    _require_file(PROCESSED_JSON, "embedding")

    from embedding import load_model, embed_texts, apply_e5_prefix, validate_chunks

    with open(PROCESSED_JSON, encoding="utf-8") as f:
        raw: list[dict] = json.load(f)
    print(f"  Loaded {len(raw)} chunks from {PROCESSED_JSON}")

    valid, skipped = validate_chunks(raw)
    if skipped:
        print(f"  [WARN] {len(skipped)} chunks skipped (empty content)")
    if not valid:
        print("[ERROR] No valid chunks to embed.")
        sys.exit(1)

    tokenizer, model = load_model()
    texts = [apply_e5_prefix(c["content"]) for c in valid]

    t0 = time.perf_counter()
    embeddings = embed_texts(texts, tokenizer, model)
    elapsed = time.perf_counter() - t0
    print(f"  Embedded {len(valid)} chunks in {elapsed:.1f}s")

    for chunk, vector in zip(valid, embeddings):
        chunk["embedding"] = vector.tolist()

    Path(EMBEDDED_JSON).parent.mkdir(parents=True, exist_ok=True)
    with open(EMBEDDED_JSON, "w", encoding="utf-8") as f:
        json.dump(valid, f, ensure_ascii=False, indent=2)
    print(f"  Saved → {EMBEDDED_JSON}")
    return valid


# ══════════════════════════════════════════════════════════════
#  STAGE 3 — vector_store → Qdrant
# ══════════════════════════════════════════════════════════════

def run_vector_store(collection: str = QDRANT_COLLECTION) -> dict:
    _sep("STAGE 3 — Vector Store (Qdrant)")
    _require_file(EMBEDDED_JSON, "vector_store")

    from vector_store import (
        build_client, ensure_collection, upsert_chunks,
        validate_chunks, infer_vector_dim, verify_store,
    )

    with open(EMBEDDED_JSON, encoding="utf-8") as f:
        raw: list[dict] = json.load(f)

    valid, skipped = validate_chunks(raw)
    if skipped:
        print(f"  [WARN] {len(skipped)} chunks without embedding skipped")
    if not valid:
        print("[ERROR] No valid chunks to index.")
        sys.exit(1)

    vector_dim = infer_vector_dim(valid)
    client     = build_client("local", "localhost", 6333, QDRANT_LOCAL_PATH)
    ensure_collection(client, collection, vector_dim)

    t0     = time.perf_counter()
    result = upsert_chunks(client, collection, valid)
    elapsed = time.perf_counter() - t0

    print(f"  Upserted {result['upserted']} chunks in {elapsed:.1f}s")
    if result["errors"]:
        print(f"  [WARN] {result['errors']} errors during upsert")

    verification = verify_store(client, collection, result["upserted"])
    print(f"  Verification: {verification['status']} "
          f"({verification['stored_count']} points in Qdrant)")
    return result


# ══════════════════════════════════════════════════════════════
#  PUBLIC API — INGESTION
# ══════════════════════════════════════════════════════════════

def ingest(
    input_path   : str,
    skip_process : bool = False,
    skip_embed   : bool = False,
    collection   : str  = QDRANT_COLLECTION,
    max_tokens   : int  = 512,
) -> dict:
    """
    Full ingestion pipeline.

    Parameters
    ----------
    input_path   : path to your math.json (or any course JSON)
    skip_process : skip Stage 1 — reuse existing embedded_chunk.json
    skip_embed   : skip Stage 1+2 — reuse existing chunks_embedded.json
    collection   : Qdrant collection name
    max_tokens   : max tokens per chunk (passed to json_process)

    Returns
    -------
    dict with status and collection name
    """
    if skip_embed:
        print("  [INFO] Skipping Stage 1+2 — using existing chunks_embedded.json")
        _require_file(EMBEDDED_JSON, "vector_store")
    elif skip_process:
        print("  [INFO] Skipping Stage 1 — using existing embedded_chunk.json")
        run_embedding()
    else:
        run_json_process(input_path, max_tokens=max_tokens)
        run_embedding()

    run_vector_store(collection)

    _sep("INGESTION COMPLETE")
    print("  Course is now indexed and ready for queries.")
    return {"status": "complete", "collection": collection}


# ══════════════════════════════════════════════════════════════
#  PUBLIC API — QUERY (STREAMING)
# ══════════════════════════════════════════════════════════════

@traceable(name="query_pipeline_stream", run_type="chain")
def query_rag_stream(
    question       : str,
    collection     : str         = QDRANT_COLLECTION,
    top_k_retrieve : int         = 10,
    filters        : dict | None = None,
) -> Generator[str, None, None]:
    """
    Streaming query: yields LLM tokens as they are generated (one LangSmith run).

    Generates from the single best `lesson` only; the other selected lessons
    are not used on this path.
    """
    ranked, lesson, lessons = _retrieve_and_rerank(
        question, collection, top_k_retrieve, filters
    )

    if not ranked:
        yield "لم يتم العثور على درس مناسب لسؤالك."
        return

    from llm import generate_answer
    yield from generate_answer(question=question, lesson=lesson, stream=True)


# ══════════════════════════════════════════════════════════════
#  PUBLIC API — QUERY (TRACED, NON-STREAMING)
# ══════════════════════════════════════════════════════════════

@traceable(name="query_pipeline", run_type="chain")
def query_rag_traced(
    question       : str,
    collection     : str         = QDRANT_COLLECTION,
    top_k_retrieve : int         = 10,
    filters        : dict | None = None,
) -> dict:
    """
    Traced query — runs the full pipeline and returns a complete string.

    Safe to use with LangSmith @traceable because it never returns a generator.
    Use query_rag_stream() instead when you need to stream tokens to the client.

    Returns
    -------
    {
        "answer"  : str,
        "lesson"  : dict,        # single best-matching lesson (unchanged shape)
        "lessons" : list[dict],  # all distinct relevant lessons found
        "latency" : float,
    }
    """
    t0 = time.perf_counter()

    ranked, lesson, lessons = _retrieve_and_rerank(
        question, collection, top_k_retrieve, filters
    )

    if not ranked:
        return {
            "answer"  : "لم يتم العثور على درس مناسب لسؤالك.",
            "lesson"  : None,
            "lessons" : [],
            "latency" : round(time.perf_counter() - t0, 3),
        }

    from llm import generate_answer

    raw    = generate_answer(question=question, lesson=lesson, stream=False)
    answer = raw if isinstance(raw, str) else "".join(raw)

    latency = time.perf_counter() - t0
    print(f"  [pipeline] Done in {latency:.2f}s  |  {len(answer)} chars")

    return {
        "answer"  : answer,
        "lesson"  : lesson,
        "lessons" : lessons,
        "latency" : round(latency, 3),
    }


# ══════════════════════════════════════════════════════════════
#  PUBLIC API — QUERY (BACKWARDS COMPATIBLE WRAPPER)
# ══════════════════════════════════════════════════════════════

def query_rag(
    question       : str,
    collection     : str         = QDRANT_COLLECTION,
    top_k_retrieve : int         = 10,
    filters        : dict | None = None,
    stream         : bool        = False,
) -> dict:
    """
    Backwards-compatible wrapper kept so existing callers don't break.

    - stream=False (default) → delegates to query_rag_traced()
    - stream=True            → collects query_rag_stream() into a string
                               and returns the same dict shape

    Prefer calling query_rag_traced() or query_rag_stream() directly.
    """
    if stream:
        t0     = time.perf_counter()
        tokens = list(query_rag_stream(question, collection, top_k_retrieve, filters))
        answer = "".join(tokens)
        return {
            "answer"  : answer,
            "lesson"  : None,   # lesson dict not available in this path
            "lessons" : [],     # not available in this path either
            "latency" : round(time.perf_counter() - t0, 3),
        }

    return query_rag_traced(question, collection, top_k_retrieve, filters)

# ══════════════════════════════════════════════════════════════
#  CLI
# ══════════════════════════════════════════════════════════════

def _cli_ingest(args):
    ingest(
        input_path   = args.input,
        skip_process = args.skip_process,
        skip_embed   = args.skip_embed,
        collection   = args.collection,
        max_tokens   = args.max_tokens,
    )


def _cli_query(args):
    if args.stream:
        t0 = time.perf_counter()
        print(f"\n{'='*60}\n  ANSWER (streaming)\n{'='*60}")
        for token in query_rag_stream(
            question       = args.question,
            collection     = args.collection,
            top_k_retrieve = args.top_k_retrieve,
        ):
            print(token, end="", flush=True)
        print(f"\n  Latency : {time.perf_counter() - t0:.2f}s")
    else:
        result = query_rag_traced(
            question       = args.question,
            collection     = args.collection,
            top_k_retrieve = args.top_k_retrieve,
        )
        print(f"\n{'='*60}\n  ANSWER\n{'='*60}")
        print(result["answer"])
        if result["lesson"]:
            print(f"\n  Lesson  : {result['lesson']['lesson_title']} (level={result['lesson']['level']})")
        lessons = result.get("lessons") or []
        if len(lessons) > 1:
            print(f"  Lessons found ({len(lessons)}):")
            for l in lessons:
                print(f"    - {l['lesson_title']} (#{l['lesson_number']})")
        print(f"  Latency : {result['latency']}s")


def main():
    parser = argparse.ArgumentParser(
        description="Prof IA — RAG pipeline orchestrator",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python pipeline.py ingest --input data/math.json
  python pipeline.py ingest --input data/math.json --skip-process
  python pipeline.py ingest --input data/math.json --skip-embed
  python pipeline.py query  --question "ما هو العدد الأولي"
  python pipeline.py query  --question "فسرلي الدرس الثاني" --stream
  python pipeline.py query  --question "Qu'est-ce qu'un nombre premier?" --no-stream
        """,
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # ── ingest ────────────────────────────────────────────────
    p_ing = sub.add_parser("ingest", help="Ingest a course JSON file")
    p_ing.add_argument("--input",        default="data/math.json",    help="Path to input JSON")
    p_ing.add_argument("--collection",   default=QDRANT_COLLECTION,   help="Qdrant collection name")
    p_ing.add_argument("--max-tokens",   type=int, default=512,       dest="max_tokens",
                                         help="Max tokens per chunk (default: 512)")
    p_ing.add_argument("--skip-process", action="store_true",         dest="skip_process",
                                         help="Reuse existing embedded_chunk.json")
    p_ing.add_argument("--skip-embed",   action="store_true",         dest="skip_embed",
                                         help="Reuse existing chunks_embedded.json")

    # ── query ─────────────────────────────────────────────────
    p_q = sub.add_parser("query", help="Query the RAG pipeline")
    p_q.add_argument("--question",       required=True)
    p_q.add_argument("--collection",     default=QDRANT_COLLECTION)
    p_q.add_argument("--top-k-retrieve", type=int, default=10,   dest="top_k_retrieve",
                                         help="Retriever candidates fed to reranker (default: 10)")
    p_q.add_argument("--stream",         action="store_true",    dest="stream",
                                         help="Stream tokens to stdout")
    p_q.add_argument("--no-stream",      action="store_false",   dest="stream",
                                         help="Blocking mode — print full answer at once")
    p_q.set_defaults(stream=False)

    args = parser.parse_args()

    if args.command == "ingest":
        _cli_ingest(args)
    elif args.command == "query":
        _cli_query(args)


if __name__ == "__main__":
    main()