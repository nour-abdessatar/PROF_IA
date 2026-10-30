"""
router_eval.py — Evaluation harness for router.py's classify().

Metric 1 of 4: Router classification accuracy + confusion matrix.

Computes:
  - exact-match accuracy on `intent` and on `mode`
  - a confusion matrix for `intent` (rows = expected, cols = predicted)
  - hard-check vs LLM-path split: was the example resolved by
    _hard_lesson_check()/_hard_exercise_check() without ever calling the
    LLM, or did it need the full classify() call?
  - a printed list of every failing example, so you can paste the
    concrete misses into your report/defense slides

Usage (run from your project root, with router.py importable):
    python router_eval.py --dataset datasets/router_eval_set.json

Requires GEMINI_API_KEY (or GOOGLE_API_KEY) in the environment — classify()
calls Gemini for anything not caught by the hard pre-checks.
"""
from __future__ import annotations

import argparse
import json
import os
from collections import Counter, defaultdict

from router import classify, _hard_lesson_check, _hard_exercise_check


def load_dataset(path: str) -> list[dict]:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def resolved_by_hard_check(question: str) -> bool:
    return (
        _hard_lesson_check(question) is not None
        or _hard_exercise_check(question) is not None
    )


def run_eval(dataset: list[dict], api_key: str) -> dict:
    intent_confusion: dict[str, Counter] = defaultdict(Counter)
    mode_correct = 0
    intent_correct = 0
    hard_path_count = 0
    per_intent_total: Counter = Counter()
    results = []

    for ex in dataset:
        question = ex["question"]
        history = ex.get("history", [])
        expected_intent = ex["expected_intent"]
        expected_mode = ex["expected_mode"]

        route = classify(question=question, history=history, api_key=api_key)

        per_intent_total[expected_intent] += 1
        intent_confusion[expected_intent][route.intent] += 1

        intent_ok = route.intent == expected_intent
        mode_ok = route.mode == expected_mode
        if intent_ok:
            intent_correct += 1
        if mode_ok:
            mode_correct += 1
        if resolved_by_hard_check(question):
            hard_path_count += 1

        results.append({
            "question": question,
            "expected_intent": expected_intent, "got_intent": route.intent,
            "expected_mode": expected_mode, "got_mode": route.mode,
            "intent_ok": intent_ok, "mode_ok": mode_ok,
        })

    n = len(dataset)
    return {
        "n": n,
        "intent_accuracy": intent_correct / n,
        "mode_accuracy": mode_correct / n,
        "hard_check_rate": hard_path_count / n,
        "confusion": {k: dict(v) for k, v in intent_confusion.items()},
        "per_intent_total": dict(per_intent_total),
        "failures": [r for r in results if not (r["intent_ok"] and r["mode_ok"])],
    }


def print_report(report: dict):
    print("\n" + "=" * 64)
    print(f"  ROUTER EVAL — {report['n']} examples")
    print("=" * 64)
    print(f"  Intent accuracy : {report['intent_accuracy']*100:.1f}%")
    print(f"  Mode accuracy   : {report['mode_accuracy']*100:.1f}%")
    print(f"  Hard-check rate : {report['hard_check_rate']*100:.1f}% "
          f"(resolved without an LLM call)")

    print("\n  Confusion matrix (rows=expected, cols=predicted):")
    intents = sorted(report["per_intent_total"].keys())
    header = "  " + " " * 18 + "".join(f"{i[:10]:>12}" for i in intents)
    print(header)
    for expected in intents:
        row = report["confusion"].get(expected, {})
        line = f"  {expected[:16]:<18}" + "".join(f"{row.get(p, 0):>12}" for p in intents)
        print(line)

    if report["failures"]:
        print(f"\n  {len(report['failures'])} failing examples:")
        for f in report["failures"][:20]:
            print(f"    Q: {f['question'][:60]!r}")
            print(f"       expected intent={f['expected_intent']} mode={f['expected_mode']}"
                  f"  |  got intent={f['got_intent']} mode={f['got_mode']}")
    print("=" * 64 + "\n")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--dataset", default="datasets/router_eval_set.json")
    p.add_argument("--out", default="router_eval_report.json")
    args = p.parse_args()

    api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY", "")
    if not api_key:
        raise SystemExit(
            "GEMINI_API_KEY not set — router.classify() needs it for any "
            "example not caught by the hard pre-checks."
        )

    dataset = load_dataset(args.dataset)
    report = run_eval(dataset, api_key)
    print_report(report)

    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(f"Full report saved to {args.out}")


if __name__ == "__main__":
    main()