"""
embedding.py — Step 2 of the indexing pipeline: turn chunks into vectors.

    json_process.py  ->  embedding.py  ->  vector_store.py

Reads   output/embedded_chunk.json   (chunks produced by json_process.py)
Writes  output/chunks_embedded.json  (the same chunks + an "embedding" field)

Model: intfloat/multilingual-e5-base (768 dimensions). Each passage gets the
"passage: " prefix the E5 models expect, and vectors are L2-normalised so that
cosine similarity is a plain dot product.

Usage:
    python embedding.py [input.json] [output.json]
"""

import json
import sys
import time
from pathlib import Path

import numpy as np
import torch
from transformers import AutoModel, AutoTokenizer

# ─────────────────────────── CONFIG ────────────────────────────

INPUT_FILE  = "output/embedded_chunk.json"
OUTPUT_FILE = "output/chunks_embedded.json"

EMBEDDING_MODEL = "intfloat/multilingual-e5-base"
BATCH_SIZE      = 32
# Model limit, including the 2 special tokens and the prefix below. Longer
# inputs are silently truncated by the tokenizer (see DEFAULT_MAX_TOKENS in
# json_process.py).
MAX_LENGTH      = 512
E5_DOC_PREFIX   = "passage: "

if torch.cuda.is_available():
    DEVICE = torch.device("cuda")
elif torch.backends.mps.is_available():
    DEVICE = torch.device("mps")
else:
    DEVICE = torch.device("cpu")


# ─────────────────────── MODEL LOADING ─────────────────────────

def load_model() -> tuple[AutoTokenizer, AutoModel]:
    """Load the E5 tokenizer and model onto DEVICE, in eval mode."""
    print(f"[INFO] Loading model '{EMBEDDING_MODEL}'…")
    print(f"[INFO] Device: {DEVICE}")
    tokenizer = AutoTokenizer.from_pretrained(EMBEDDING_MODEL)
    model     = AutoModel.from_pretrained(EMBEDDING_MODEL)
    model.eval()
    model.to(DEVICE)
    print("[OK]  Model loaded")
    return tokenizer, model


# ─────────────────────── EMBEDDING UTILS ───────────────────────

def _average_pool(last_hidden: torch.Tensor, attention_mask: torch.Tensor) -> torch.Tensor:
    """Mean of the token vectors, ignoring padding positions."""
    mask_expanded = attention_mask.unsqueeze(-1).float()
    summed        = (last_hidden * mask_expanded).sum(dim=1)
    counts        = mask_expanded.sum(dim=1).clamp(min=1e-9)
    return summed / counts


def _normalize(vectors: np.ndarray) -> np.ndarray:
    """L2-normalise each row (zero vectors are left unchanged)."""
    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    norms = np.where(norms == 0, 1.0, norms)
    return vectors / norms


def embed_texts(
    texts: list[str],
    tokenizer: AutoTokenizer,
    model: AutoModel,
    batch_size: int = BATCH_SIZE,
    show_progress: bool = True,
) -> np.ndarray:
    """Encode texts in batches; returns a (len(texts), dim) normalised matrix."""
    all_embeddings: list[np.ndarray] = []
    n_batches = (len(texts) + batch_size - 1) // batch_size

    for i in range(0, len(texts), batch_size):
        batch     = texts[i : i + batch_size]
        batch_idx = i // batch_size + 1

        if show_progress and (batch_idx % 10 == 0 or batch_idx == 1):
            print(f"  batch {batch_idx}/{n_batches} …")

        encoded = tokenizer(
            batch,
            padding=True,
            truncation=True,
            max_length=MAX_LENGTH,
            return_tensors="pt",
        )
        encoded = {k: v.to(DEVICE) for k, v in encoded.items()}

        with torch.no_grad():
            output = model(**encoded)

        embeddings = _average_pool(
            output.last_hidden_state,
            encoded["attention_mask"],
        )
        embeddings = embeddings.cpu().float().numpy()
        all_embeddings.append(embeddings)

    matrix = np.vstack(all_embeddings)
    return _normalize(matrix)


# ─────────────────────── PREFIXING ─────────────────────────────

def apply_e5_prefix(text: str) -> str:
    """Add the "passage: " prefix unless it is already there."""
    if text.startswith(E5_DOC_PREFIX):
        return text
    return E5_DOC_PREFIX + text


# ─────────────────────── VALIDATION ────────────────────────────

def validate_chunks(chunks: list[dict]) -> tuple[list[dict], list[dict]]:
    """Split chunks into (valid, skipped); a chunk without text is skipped."""
    valid, skipped = [], []
    for c in chunks:
        content = (c.get("content") or "").strip()
        if content:
            valid.append(c)
        else:
            skipped.append(c)
    return valid, skipped


# ─────────────────────────── REPORT ────────────────────────────

def print_report(
    total: int,
    skipped: int,
    embedded: int,
    elapsed: float,
    dim: int,
    chunks: list[dict],
) -> None:
    """Print a summary of the run (counts, speed, chunks per level/subject)."""
    print("\n" + "=" * 60)
    print("  EMBEDDING REPORT")
    print("=" * 60)
    print(f"  Chunks read            : {total}")
    print(f"  Skipped (no text)      : {skipped}")
    print(f"  Embeddings produced    : {embedded}")
    print(f"  Vector dimension       : {dim}")
    if embedded:
        print(f"  Total time             : {elapsed:.1f}s  "
              f"({elapsed / embedded * 1000:.1f} ms/chunk)")
    print(f"  Device                 : {DEVICE}")

    levels: dict[str, int] = {}
    subjects: dict[str, int] = {}
    for c in chunks:
        lvl = c.get("level", "?") or "?"
        sub = c.get("subject", "?") or "?"
        levels[lvl]   = levels.get(lvl, 0) + 1
        subjects[sub] = subjects.get(sub, 0) + 1

    print(f"\n  Levels   : {levels}")
    print(f"  Subjects : {subjects}")

    missing = sum(1 for c in chunks if "embedding" not in c)
    if missing:
        print(f"\n  [!] Chunks without embedding: {missing}")
    else:
        print("\n  All chunks have an embedding ✓")
    print("=" * 60 + "\n")


def validate_sample(chunks: list[dict], n: int = 3) -> None:
    """Print the first n chunks with their vector norm (should be ~1.0)."""
    print("=" * 60)
    print("  EMBEDDING SAMPLE")
    print("=" * 60)
    for c in chunks[:n]:
        emb  = c.get("embedding", [])
        norm = float(np.linalg.norm(emb)) if emb else 0.0
        print(
            f"\n  chunk_id      = {c.get('chunk_id')}\n"
            f"  level         = {c.get('level')}\n"
            f"  subject       = {c.get('subject')}\n"
            f"  lesson_number = {c.get('lesson_number')}\n"
            f"  lesson_title  = {c.get('lesson_title')}\n"
            f"  dim={len(emb)}  norm={norm:.6f}  (should be ~1.0)"
        )
        print("  content[:120]:")
        print(f"    {c.get('content', '')[:120]}")
    print("=" * 60 + "\n")


# ─────────────────────────── MAIN ──────────────────────────────

def main() -> None:
    args     = sys.argv[1:]
    src      = args[0] if len(args) > 0 else INPUT_FILE
    dst      = args[1] if len(args) > 1 else OUTPUT_FILE
    src_path = Path(src)

    if not src_path.exists():
        print(f"[ERROR] File not found: {src}")
        sys.exit(1)

    # Read
    with open(src_path, encoding="utf-8") as f:
        raw: list[dict] = json.load(f)
    print(f"[INFO] Loaded {len(raw)} chunks from {src}")

    # Validate
    valid, skipped = validate_chunks(raw)
    if skipped:
        print(f"[WARN] {len(skipped)} chunks skipped (empty content):")
        for c in skipped[:5]:
            print(f"       chunk_id={c.get('chunk_id')}  lesson={c.get('lesson_number')}")

    if not valid:
        print("[ERROR] No valid chunk to encode.")
        sys.exit(1)

    # Load model
    tokenizer, model = load_model()
    dim = model.config.hidden_size
    print(f"[INFO] Embedding dimension: {dim}")

    # E5 prefix
    texts = [apply_e5_prefix(c["content"]) for c in valid]
    print(f"[INFO] Prefixed example: {texts[0][:100]!r}")

    # Encode
    print(f"[INFO] Encoding {len(texts)} chunks (batch={BATCH_SIZE})…")
    t0         = time.perf_counter()
    embeddings = embed_texts(texts, tokenizer, model)
    elapsed    = time.perf_counter() - t0
    print(f"[OK]  Encoding finished in {elapsed:.1f}s")

    # Attach each vector to its chunk
    for chunk, vector in zip(valid, embeddings):
        chunk["embedding"] = vector.tolist()

    # Save
    Path(dst).parent.mkdir(parents=True, exist_ok=True)
    with open(dst, "w", encoding="utf-8") as f:
        json.dump(valid, f, ensure_ascii=False, indent=2)
    print(f"[OK]  Saved to {dst}  ({len(valid)} chunks)")

    print_report(len(raw), len(skipped), len(valid), elapsed, dim, valid)
    validate_sample(valid)


if __name__ == "__main__":
    main()