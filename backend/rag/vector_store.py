"""
vector_store.py — step 7 of the RAG pipeline.

Reads output/chunks_embedded.json, creates or checks the Qdrant collection
(local folder, server or in-memory), upserts vectors + payloads in batches,
verifies the result and writes output/vector_store_report.json.

    json_process -> embedding -> vector_store -> retriever

CLI:
    python vector_store.py                                   # local mode (default)
    python vector_store.py --mode server --host localhost --port 6333
    python vector_store.py --input output/chunks_embedded.json --collection tunisian_college
"""

import argparse
import hashlib
import json
import sys
import time
from pathlib import Path
from typing import Any

from qdrant_client import QdrantClient
from qdrant_client.http import models as qmodels

# ─────────────────────────── CONFIG ────────────────────────────

INPUT_FILE       = "output/chunks_embedded.json"
REPORT_FILE      = "output/vector_store_report.json"
LOCAL_DB_PATH    = "output/qdrant_local"

DEFAULT_COLLECTION = "tunisian_college"

BATCH_SIZE       = 128
DISTANCE_METRIC  = qmodels.Distance.COSINE

EXCLUDED_PAYLOAD_FIELDS = {"embedding"}


# ─────────────────────────── CLIENT ────────────────────────────

def build_client(mode: str, host: str, port: int, local_path: str) -> QdrantClient:
    if mode == "local":
        Path(local_path).mkdir(parents=True, exist_ok=True)
        print(f"[INFO] LOCAL mode  — database: {local_path}")
        return QdrantClient(path=local_path)

    if mode == "server":
        url = f"http://{host}:{port}"
        print(f"[INFO] SERVER mode — {url}")
        return QdrantClient(host=host, port=port)

    if mode == "memory":
        print("[INFO] MEMORY mode (tests)")
        return QdrantClient(":memory:")

    raise ValueError(f"Unknown mode: {mode!r}  (local | server | memory)")


# ─────────────────────── COLLECTION ────────────────────────────

def ensure_collection(client: QdrantClient, collection: str, vector_dim: int) -> bool:
    existing = [c.name for c in client.get_collections().collections]

    if collection in existing:
        info = client.get_collection(collection)
        stored_dim = info.config.params.vectors.size
        if stored_dim != vector_dim:
            raise ValueError(
                f"Collection '{collection}' exists with dim={stored_dim} "
                f"but the embeddings have dim={vector_dim}. "
                f"Delete the collection or use another name."
            )
        print(f"[OK]  Collection '{collection}' already exists  (dim={stored_dim})")
        return True

    client.create_collection(
        collection_name=collection,
        vectors_config=qmodels.VectorParams(
            size=vector_dim,
            distance=DISTANCE_METRIC,
        ),
    )
    print(f"[OK]  Collection '{collection}' created  (dim={vector_dim}, distance=COSINE)")
    return False


# ─────────────────────── POINT ID ──────────────────────────────

def chunk_to_point_id(chunk: dict) -> int:
    raw = chunk.get("chunk_id")
    if isinstance(raw, int):
        return raw
    digest = hashlib.sha256(str(raw).encode()).digest()
    return int.from_bytes(digest[:8], "big") & 0x7FFF_FFFF_FFFF_FFFF


# ─────────────────────── VALIDATION ────────────────────────────

def validate_chunks(chunks: list[dict]) -> tuple[list[dict], list[dict]]:
    valid, skipped = [], []
    for c in chunks:
        emb = c.get("embedding")
        if emb and isinstance(emb, list) and len(emb) > 0:
            valid.append(c)
        else:
            skipped.append(c)
    return valid, skipped


def infer_vector_dim(chunks: list[dict]) -> int:
    for c in chunks:
        emb = c.get("embedding")
        if emb:
            return len(emb)
    raise ValueError("Cannot infer the vector dimension: no embedding found.")


# ─────────────────────── PAYLOAD ───────────────────────────────

def build_payload(chunk: dict) -> dict[str, Any]:
    payload = {}
    for key, value in chunk.items():
        if key in EXCLUDED_PAYLOAD_FIELDS:
            continue
        if isinstance(value, (str, int, float, bool, list, dict, type(None))):
            payload[key] = value
        else:
            payload[key] = str(value)
    return payload


# ─────────────────────── UPSERT ────────────────────────────────

def upsert_chunks(
    client: QdrantClient,
    collection: str,
    chunks: list[dict],
    batch_size: int = BATCH_SIZE,
) -> dict[str, int]:
    n_batches = (len(chunks) + batch_size - 1) // batch_size
    total_ok  = 0
    total_err = 0
    error_ids = []

    print(f"[INFO] Upserting {len(chunks)} chunks in {n_batches} batch(es) …")

    for i in range(0, len(chunks), batch_size):
        batch     = chunks[i : i + batch_size]
        batch_idx = i // batch_size + 1

        points = []
        for c in batch:
            try:
                points.append(
                    qmodels.PointStruct(
                        id      = chunk_to_point_id(c),
                        vector  = c["embedding"],
                        payload = build_payload(c),
                    )
                )
            except Exception as exc:
                print(f"  [WARN] Chunk skipped: chunk_id={c.get('chunk_id')}  err={exc}")
                total_err += 1
                error_ids.append(c.get("chunk_id"))

        if not points:
            continue

        try:
            client.upsert(
                collection_name = collection,
                points          = points,
                wait            = True,
            )
            total_ok += len(points)

            if batch_idx % 10 == 0 or batch_idx == n_batches:
                print(f"  batch {batch_idx}/{n_batches}  "
                      f"({total_ok} upserted, {total_err} errors)")

        except Exception as exc:
            print(f"  [ERROR] Batch {batch_idx} failed: {exc}")
            total_err += len(points)
            error_ids.extend(c.get("chunk_id") for c in batch)

    return {"upserted": total_ok, "errors": total_err, "error_ids": error_ids}


# ─────────────────────── VERIFICATION ──────────────────────────

def verify_store(client: QdrantClient, collection: str, expected: int) -> dict:
    info   = client.get_collection(collection)
    stored = info.points_count

    status = "OK" if stored >= expected else "PARTIEL"
    print(f"[VERIFY] Points stored: {stored} / expected: {expected}  → {status}")

    samples = client.scroll(
        collection_name = collection,
        limit           = 3,
        with_vectors    = True,
        with_payload    = True,
    )[0]

    sample_info = []
    for pt in samples:
        p = pt.payload or {}
        sample_info.append({
            "id"           : pt.id,
            "vector_dim"   : len(pt.vector) if pt.vector else 0,
            "payload_keys" : list(p.keys()),
            "chunk_id"     : p.get("chunk_id"),
            "lesson_id"    : p.get("lesson_id"),
            "level"        : p.get("level"),
            "subject"      : p.get("subject"),
        })

    return {
        "status"       : status,
        "stored_count" : stored,
        "expected"     : expected,
        "samples"      : sample_info,
    }


# ─────────────────────── REPORT ────────────────────────────────

def print_report(report: dict):
    print("\n" + "=" * 60)
    print("  VECTOR STORE REPORT")
    print("=" * 60)
    r = report
    print(f"  Collection          : {r['collection']}")
    print(f"  Mode                : {r['mode']}")
    print(f"  Chunks read         : {r['total_read']}")
    print(f"  Skipped (no emb.)   : {r['skipped']}")
    print(f"  Upserted ✓          : {r['upserted']}")
    print(f"  Errors              : {r['errors']}")
    print(f"  Total time          : {r['elapsed_s']:.1f}s")
    print(f"  Throughput          : {r['chunks_per_sec']:.1f} chunks/s")
    v = r.get("verification", {})
    print(f"  Verification        : {v.get('status')}  "
          f"({v.get('stored_count')} points in Qdrant)")
    if r["errors"]:
        print(f"  Failed IDs          : {r['error_ids'][:10]}")
    print("=" * 60 + "\n")


# ─────────────────────────── MAIN ──────────────────────────────

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="RAG pipeline — vector_store step")
    p.add_argument("--input",      default=INPUT_FILE,          help="Embedded chunks JSON file")
    p.add_argument("--collection", default=DEFAULT_COLLECTION,  help="Qdrant collection name")
    p.add_argument("--mode",       default="local",
                   choices=["local", "server", "memory"],       help="Qdrant connection mode")
    p.add_argument("--host",       default="localhost",          help="Qdrant host (server mode)")
    p.add_argument("--port",       type=int, default=6333,       help="Qdrant port (server mode)")
    p.add_argument("--local-path", default=LOCAL_DB_PATH,       help="Local database folder")
    p.add_argument("--batch-size", type=int, default=BATCH_SIZE, help="Points per upsert request")
    p.add_argument("--report",     default=REPORT_FILE,          help="JSON report file")
    return p.parse_args()


def main():
    args = parse_args()

    src = Path(args.input)
    if not src.exists():
        print(f"[ERROR] File not found: {src}")
        sys.exit(1)

    with open(src, encoding="utf-8") as f:
        raw: list[dict] = json.load(f)
    print(f"[INFO] Loaded {len(raw)} chunks from {src}")

    valid, skipped = validate_chunks(raw)
    if skipped:
        print(f"[WARN] {len(skipped)} chunks without embedding skipped:")
        for c in skipped[:5]:
            print(f"       chunk_id={c.get('chunk_id')}")

    if not valid:
        print("[ERROR] No valid chunk to index.")
        sys.exit(1)

    vector_dim = infer_vector_dim(valid)
    print(f"[INFO] Vector dimension : {vector_dim}")
    print(f"[INFO] Target collection: {args.collection}")

    client = build_client(args.mode, args.host, args.port, args.local_path)
    ensure_collection(client, args.collection, vector_dim)

    t0      = time.perf_counter()
    result  = upsert_chunks(client, args.collection, valid, args.batch_size)
    elapsed = time.perf_counter() - t0

    print(f"[OK]  Upsert finished in {elapsed:.1f}s "
          f"({result['upserted']} OK, {result['errors']} errors)")

    verification = verify_store(client, args.collection, result["upserted"])

    report = {
        "collection"     : args.collection,
        "mode"           : args.mode,
        "input_file"     : str(src),
        "total_read"     : len(raw),
        "skipped"        : len(skipped),
        "upserted"       : result["upserted"],
        "errors"         : result["errors"],
        "error_ids"      : result["error_ids"],
        "elapsed_s"      : round(elapsed, 2),
        "chunks_per_sec" : round(result["upserted"] / elapsed, 1) if elapsed > 0 else 0,
        "vector_dim"     : vector_dim,
        "verification"   : verification,
    }

    Path(args.report).parent.mkdir(parents=True, exist_ok=True)
    with open(args.report, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(f"[OK]  Report saved → {args.report}")

    print_report(report)


if __name__ == "__main__":
    main()