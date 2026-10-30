"""
groundedness_eval.py — LLM-as-judge evaluation: does the generated
[TEACHER] text stay grounded in the retrieved lesson content, with no
fabricated facts/rules from outside it?

Metric 4 of 4: Groundedness (LLM-as-judge).

BATCHED VERSION: instead of 1 Gemini call per example, this packs
`--batch-size` examples into a single call and asks the judge to return
a JSON array of verdicts, one per example (matched by "index"). This
cuts your request count ~batch_size-fold, which is what actually
matters for free-tier rate limits (they're request/minute limited,
not token limited).

Usage:
    python groundedness_eval.py --dataset datasets/groundedness_live_set.json --mode live --batch-size 6
    python groundedness_eval.py --dataset datasets/groundedness_replay_set.json --mode replay --batch-size 6
"""
from __future__ import annotations

import argparse
import json
import os
import re
import time

from google import genai
from google.genai import types

JUDGE_MODEL = "gemini-2.5-flash-lite"

_JUDGE_SYSTEM = """
You are grading whether a math tutor's spoken explanations ("TEACHER_OUTPUT")
stay grounded in the reference lesson content given ("LESSON_CONTENT"),
for a Tunisian 7th-grade math course (Arabic/French).

Grounded means: every mathematical fact, rule, or definition stated in
TEACHER_OUTPUT is either explicitly present in LESSON_CONTENT, or is a
basic, uncontested prerequisite (e.g. basic arithmetic) that any 7th grader
already knows and that is not itself the subject being taught right now.

Not grounded means: TEACHER_OUTPUT states a rule, formula, or fact that
does not appear in LESSON_CONTENT and is not a trivial prerequisite.

You will be given a JSON array of examples, each with an "index",
"question", "lesson_content", and "teacher_output". Grade EACH ONE
independently.

Return ONLY a raw JSON array, no markdown, no preamble, no code fences.
One object per input example, in the SAME ORDER, each shaped exactly like:
{
  "index": <same index as input>,
  "grounded": true | false,
  "confidence": "high" | "medium" | "low",
  "reason": "<one short sentence, in English, explaining the verdict>"
}
"""

_JSON_ARRAY_RE = re.compile(r"\[.*\]", re.S)


def _get_client() -> genai.Client:
    api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY", "")
    if not api_key:
        raise SystemExit("GEMINI_API_KEY not set")
    return genai.Client(api_key=api_key)


def _chunks(lst, size):
    for i in range(0, len(lst), size):
        yield lst[i:i + size]


def judge_batch(client: genai.Client, batch: list[dict]) -> list[dict]:
    """batch: list of {"index", "question", "lesson_content", "teacher_output"}"""
    payload = json.dumps(batch, ensure_ascii=False, indent=2)
    user_msg = f"EXAMPLES:\n{payload}"

    response = client.models.generate_content(
        model=JUDGE_MODEL,
        contents=[types.Content(role="user", parts=[types.Part(text=user_msg)])],
        config=types.GenerateContentConfig(
            system_instruction=_JUDGE_SYSTEM,
            max_output_tokens=200 * len(batch) + 100,  # room for every verdict
            temperature=0.0,
            response_mime_type="application/json",
        ),
    )
    text = response.text or ""
    m = _JSON_ARRAY_RE.search(text)
    if not m:
        # fall back: mark whole batch unparseable rather than losing it silently
        return [
            {"index": ex["index"], "grounded": None, "confidence": "low",
             "reason": f"judge returned unparseable batch output: {text[:200]!r}"}
            for ex in batch
        ]
    try:
        parsed = json.loads(m.group())
    except json.JSONDecodeError:
        return [
            {"index": ex["index"], "grounded": None, "confidence": "low",
             "reason": f"judge batch JSON parse failed: {text[:200]!r}"}
            for ex in batch
        ]

    # map back by index in case the model reorders anything
    by_index = {v.get("index"): v for v in parsed if isinstance(v, dict)}
    results = []
    for ex in batch:
        v = by_index.get(ex["index"])
        if v is None:
            results.append({"index": ex["index"], "grounded": None, "confidence": "low",
                             "reason": "judge omitted this index from batch response"})
        else:
            results.append(v)
    return results


def run_live(dataset: list[dict], client: genai.Client, batch_size: int, sleep_s: float) -> list[dict]:
    from llm import generate_answer
    from stream_splitter import split_stream, DoneEvent

    prepped = []
    for i, ex in enumerate(dataset):
        question = ex["question"]
        lesson = ex["lesson"]
        mode = ex.get("mode", "explanation")

        raw = generate_answer(question=question, lesson=lesson, stream=True, mode=mode)       
        text = raw if isinstance(raw, str) else "".join(raw)      
        events = list(split_stream(iter([text])))
        done = next((e for e in events if isinstance(e, DoneEvent)), None)
        teacher_output = done.teacher if done else ""

        prepped.append({
            "index": i, "question": question,
            "lesson_content": lesson.get("content", ""),
            "teacher_output": teacher_output,
        })

    return _judge_all(prepped, client, batch_size, sleep_s)


def run_replay(dataset: list[dict], client: genai.Client, batch_size: int, sleep_s: float) -> list[dict]:
    prepped = [
        {"index": i, "question": ex["question"],
         "lesson_content": ex["lesson_content"], "teacher_output": ex["teacher_output"]}
        for i, ex in enumerate(dataset)
    ]
    return _judge_all(prepped, client, batch_size, sleep_s)


def _judge_all(prepped: list[dict], client: genai.Client, batch_size: int, sleep_s: float) -> list[dict]:
    results_by_index = {}
    for batch in _chunks(prepped, batch_size):
        verdicts = judge_batch(client, batch)
        for ex, v in zip(batch, verdicts):
            results_by_index[ex["index"]] = {
                "question": ex["question"],
                "teacher_output": ex["teacher_output"],
                "grounded": v.get("grounded"),
                "confidence": v.get("confidence"),
                "reason": v.get("reason"),
            }
        if sleep_s:
            time.sleep(sleep_s)  # small pause between batches, extra RPM safety margin

    return [results_by_index[i] for i in sorted(results_by_index)]


def print_report(results: list[dict]):
    scored = [r for r in results if r["grounded"] is not None]
    grounded = sum(1 for r in scored if r["grounded"])
    print("\n" + "=" * 64)
    print(f"  GROUNDEDNESS EVAL (LLM-as-judge, {JUDGE_MODEL})")
    print("=" * 64)
    print(f"  Scored          : {len(scored)} / {len(results)}")
    if scored:
        print(f"  Grounded rate   : {grounded/len(scored)*100:.1f}%")
    ungrounded = [r for r in scored if not r["grounded"]]
    if ungrounded:
        print(f"\n  {len(ungrounded)} ungrounded examples:")
        for r in ungrounded[:10]:
            print(f"    Q: {r['question'][:60]!r}")
            print(f"       reason: {r['reason']}")
    unparsed = [r for r in results if r["grounded"] is None]
    if unparsed:
        print(f"\n  {len(unparsed)} unparseable / omitted — consider rerunning with a smaller batch size")
    print("=" * 64 + "\n")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--dataset", required=True)
    p.add_argument("--mode", choices=["live", "replay"], default="live")
    p.add_argument("--batch-size", type=int, default=6,
                    help="examples packed per Gemini call (default 6; try 5-8)")
    p.add_argument("--sleep", type=float, default=1.0,
                    help="seconds to sleep between batches (rate-limit safety margin)")
    p.add_argument("--out", default="groundedness_eval_report.json")
    args = p.parse_args()

    with open(args.dataset, encoding="utf-8") as f:
        dataset = json.load(f)

    client = _get_client()
    if args.mode == "live":
        results = run_live(dataset, client, args.batch_size, args.sleep)
    else:
        results = run_replay(dataset, client, args.batch_size, args.sleep)
    print_report(results)

    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print(f"Full report saved to {args.out}")


if __name__ == "__main__":
    main()