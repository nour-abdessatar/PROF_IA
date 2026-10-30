# Evaluation suite — RAG math tutoring system

Four metrics, one per major pipeline stage, as agreed for the PFE scope:

| # | Metric | Stage evaluated | Script | Needs API key? |
|---|--------|------------------|--------|-----------------|
| 1 | Router classification accuracy + confusion matrix | `router.py` | `router_eval.py` | Yes (Gemini) |
| 2 | Retrieval Recall@k + MRR (+ multi-topic recall) | `pipeline.py` / `retriever.py` / `reranker.py` | `retrieval_eval.py` | No (local Qdrant + embedding model only) |
| 3 | Canvas widget JSON validity rate | `llm.py` + `stream_handler.py` | `canvas_validity_eval.py` | Yes (Gemini) |
| 4 | Groundedness (LLM-as-judge) | `llm.py` + `prompt_builder.py` output quality | `groundedness_eval.py` | Yes (Gemini) |

## Setup

1. Copy the whole `eval/` folder into your project root (same level as `router.py`, `pipeline.py`, etc.) so the imports resolve.
2. Make sure `GEMINI_API_KEY` (or `GOOGLE_API_KEY`) is set in your environment for metrics 1, 3, 4.
3. Make sure your Qdrant collection is already ingested for metric 2 (it queries your real indexed lessons).

```bash
cd eval
export GEMINI_API_KEY=your_key_here

python router_eval.py --dataset datasets/router_eval_set.json
python retrieval_eval.py --dataset datasets/retrieval_eval_set.json --k 5
python canvas_validity_eval.py --dataset datasets/canvas_eval_set.json
python groundedness_eval.py --dataset datasets/groundedness_live_set.json --mode live
```

Each script prints a readable report to stdout AND saves a full JSON report (`*_report.json`) you can attach as an appendix or turn into a chart for your slides.

## What each metric actually tells you (for your defense)

**1. Router accuracy** — shows your intent/mode classification is reliable before any expensive retrieval or generation happens. The confusion matrix specifically targets `exercise` vs `exercise_answer`, which your own development history shows was a real, recurring source of bugs (correction requests like "اعطني إصلاح تمرين 2" being misrouted) — a great "we found this failure mode and now measure it" story.

**2. Retrieval Recall@k / MRR** — shows your hybrid dense+BM25 retrieval with reranking actually surfaces the correct lesson for a given question. The multi-topic recall sub-metric specifically checks whether a two-topic query (e.g. "تمارين حول القوة والقسمة الإقليدية") returns BOTH lessons, not just the top-scoring one — again tied to a real fix you made (`select_relevant_lessons`).

**3. Canvas widget validity rate** — shows how reliable the LLM is at producing well-formed STEPS JSON for your server-owned canvas engine. A high first-try validity rate is a strong signal that your prompt engineering (explicit schema, worked examples in `prompt_builder.py`) is working; any invalid cases show exactly which lesson/diagram shape still needs prompt tuning.

**4. Groundedness (LLM-as-judge)** — shows the model isn't fabricating math content outside what was retrieved from the curriculum. This is the metric to spend the most explanation time on in your defense, since it demonstrates you understand *why* exact-string metrics don't work for free-text generation, and that you built a proper judge rubric instead of just eyeballing outputs.

## Suggested slide structure

1. One slide per metric: definition → your number → 1-2 concrete example failures (pulled straight from the `failures` list in each JSON report).
2. A closing slide connecting metrics to your actual bug-fix history: "metric X exists because we hit bug Y in production, this is how we now catch regressions of it."

## Comparing two system variants (e.g. vector_rag7 = svg vs vector_rag8 = canvas)

If you have multiple copies of the system under `data/` (each a full copy
with its own `router.py`, `pipeline.py`, etc.), place this `eval/` folder as
a **sibling** of those copies, not inside either one:

```
RAG/data/
├── vector_rag7/     (svg variant)
├── vector_rag8/     (canvas variant)
└── eval/             <- this folder
```

No code changes are needed in either variant. Each eval script imports
`router`/`pipeline`/`llm`/etc. by plain module name, which Python resolves
through `PYTHONPATH`. `run_compare.sh` sets `PYTHONPATH` to each variant's
directory in turn, runs all 4 scripts against it, then calls
`compare_systems.py` to print a side-by-side table with a per-metric winner:

```bash
cd eval
chmod +x run_compare.sh
export GEMINI_API_KEY=your_key_here
./run_compare.sh vector_rag7 vector_rag8
```

Each variant's reports land in `reports/<system_name>/`, so you keep a full
history and can re-run the comparison any time with just:

```bash
python compare_systems.py --systems vector_rag7 vector_rag8 --reports-dir reports
```

**Important caveat for the canvas metric**: if one variant is SVG-based
(no STEPS-JSON canvas engine), `canvas_validity_eval.py` will correctly
report `N/A` for it (`n_canvas_evaluated: 0`) — that's expected, not a bug.
Don't count that metric against the SVG variant when picking a winner;
`compare_systems.py` already excludes N/A systems from that metric's win
tally. If you want an equivalent check for the SVG variant, the natural
addition is a small `svg_validity_eval.py` that parses the raw SVG/HTML
`[WIDGET]` output and checks structural rules from `prompt_builder.py`
(e.g. every drawn point has an adjacent `<text>` label) — optional, not
required for the PFE scope already agreed on.

## Extending later (optional, not required for PFE scope)

- Swap `groundedness_eval.py`'s `run_live` for `run_replay` once you have real LangSmith traces to pull `(question, lesson_content, teacher_output)` triples from — evaluating real production output is more convincing than only fresh generations.
- Track these same 4 numbers over time (e.g. one JSON report per week) to show regression-testing, not just a one-time score.