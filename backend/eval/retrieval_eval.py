"""
retrieval_eval.py — Evaluation harness for pipeline._retrieve_and_rerank().

Metric 2 of 4: Retrieval Recall@k + MRR (+ multi-topic recall).

Computes:
  - Recall@k         : is at least one expected lesson present among the
                        top-k reranked chunks?
  - MRR               : mean reciprocal rank of the first chunk matching
                        ANY expected lesson, across the full ranked list.
  - Multi-topic recall: for queries with MORE than one expected lesson
                        (e.g. "تمارين حول القوة والقسمة الإقليدية"), did
                        select_relevant_lessons() actually return ALL of
                        them, or did it silently collapse to just the top
                        match? This is exactly the failure mode pipeline.py
                        v9-v12 were built to fix — worth tracking on its own.

Usage:
    python retrieval_eval.py --dataset datasets/retrieval_eval_set.json --k 5
"""
from __future__ import annotations

import argparse
import json

from pipeline import _retrieve_and_rerank, QDRANT_COLLECTION


def load_dataset(path: str) -> list[dict]:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def reciprocal_rank(ranked_chunks, expected_numbers: set[str]) -> float:
    for i, chunk in enumerate(ranked_chunks, start=1):
        if str(getattr(chunk, "lesson_number", "")) in expected_numbers:
            return 1.0 / i
    return 0.0


def run_eval(dataset: list[dict], k: int) -> dict:
    n = len(dataset)
    hits_at_k = 0
    mrr_sum = 0.0
    multi_topic_total = 0
    multi_topic_full_recall = 0
    failures = []

    for ex in dataset:
        question = ex["question"]
        expected = set(str(x) for x in ex["expected_lesson_numbers"])

        ranked, lesson, lessons = _retrieve_and_rerank(
            question=question, collection=QDRANT_COLLECTION, top_k_retrieve=10,
        )

        top_k_numbers = {str(getattr(c, "lesson_number", "")) for c in ranked[:k]}
        hit = bool(top_k_numbers & expected)
        if hit:
            hits_at_k += 1
        else:
            failures.append({
                "question": question,
                "expected": sorted(expected),
                "got_top_k": sorted(top_k_numbers),
            })

        mrr_sum += reciprocal_rank(ranked, expected)

        if len(expected) > 1:
            multi_topic_total += 1
            got_numbers = {str(l.get("lesson_number")) for l in lessons}
            if expected.issubset(got_numbers):
                multi_topic_full_recall += 1

    return {
        "n": n, "k": k,
        "recall_at_k": hits_at_k / n,
        "mrr": mrr_sum / n,
        "multi_topic_total": multi_topic_total,
        "multi_topic_full_recall_rate": (
            multi_topic_full_recall / multi_topic_total if multi_topic_total else None
        ),
        "failures": failures,
    }


def print_report(report: dict):
    print("\n" + "=" * 64)
    print(f"  RETRIEVAL EVAL — {report['n']} queries, k={report['k']}")
    print("=" * 64)
    print(f"  Recall@{report['k']:<2}         : {report['recall_at_k']*100:.1f}%")
    print(f"  MRR               : {report['mrr']:.3f}")
    if report["multi_topic_total"]:
        print(f"  Multi-topic recall: {report['multi_topic_full_recall_rate']*100:.1f}% "
              f"({report['multi_topic_total']} multi-topic queries)")
    if report["failures"]:
        print(f"\n  {len(report['failures'])} misses:")
        for f in report["failures"][:15]:
            print(f"    Q: {f['question'][:60]!r}  expected={f['expected']}  "
                  f"got={f['got_top_k']}")
    print("=" * 64 + "\n")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--dataset", default="datasets/retrieval_eval_set.json")
    p.add_argument("--k", type=int, default=5)
    p.add_argument("--out", default="retrieval_eval_report.json")
    args = p.parse_args()

    dataset = load_dataset(args.dataset)
    report = run_eval(dataset, args.k)
    print_report(report)

    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(f"Full report saved to {args.out}")


if __name__ == "__main__":
    main()