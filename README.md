# Causal Rationale Extraction System

Answers natural-language questions about **why escalations, refunds and churn happen** in agent-customer call
transcripts. Each answer is backed by statistics and cited dialogue turns, and follow-up questions (refine,
drill down, new topic) stay grounded in the same evidence.

The pipeline is deliberately **not** a retrieval chatbot. A question selects a group of calls, the group is compared
statistically with a control group, the finding is attributed to specific turns by leave-one-turn-out ablation, and only
then does a language model write a short explanation from that structured evidence.

## Repository layout

```
src/        pipeline code (normalize, labeling, features, compare, explain) and label-validation scripts
scripts/    run_submission.py (batch run), evaluate.py (metrics), demo.py, patch_explain.py, report/deck builders
outputs/    query results, session transcripts, query_dataset.csv, eval_metrics.json, eval_report.md
report/     technical report (PDF)
deck/       presentation (PPTX and PDF)
tests/      unit tests
queries.txt the hand-written query set
```

## Data

The transcript dataset is copyrighted and licensed for this hackathon only, so **it is not included in this
repository**. Place the raw file at `data/raw/final_transcripts.json`. The pipeline writes its intermediate tables to
`data/processed/` (git-ignored).

`src/explain.py` expects these processed tables:

| File | Content |
|---|---|
| `data/processed/calls_labeled_v3.parquet` | calls with silver outcome labels |
| `data/processed/call_features.parquet` | call-level behavioural features |
| `data/processed/turn_features.parquet` | turn-level features |
| `data/processed/turns_normalized.parquet` | cleaned turns with text |

## Setup

Python 3.10 or newer.

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

### Choosing the answer writer (Stage 7)

Stage 7 needs an LLM to turn the evidence into prose. Set **one** of these; with neither, the system still runs and
prints the evidence bundle it would have sent.

```bash
export ANTHROPIC_API_KEY=...                       # Claude (default model in src/explain.py)
# or
pip install groq && export GROQ_API_KEY=...        # open model via Groq
export GROQ_MODEL=...                              # optional, defaults to llama-3.3-70b-versatile
```

If you only have a Groq key, run `python3 scripts/patch_explain.py` once. It adds the Groq fallback, synonym handling and
the multi-outcome guard to `src/explain.py` (it keeps a backup at `src/explain.py.orig`).

## Pipeline

| Stage | What it does | Code |
|---|---|---|
| 0 Normalize | strip meta turns, resolve speaker roles, flag uncertain calls | `src/normalize.py` |
| 1 Label | silver outcome labels from several signals, confidence-tiered | `src/labeling_v3.py` |
| 2 Features | emotion and dialogue-act signals, aggregated per call | `src/features.py` |
| 4 Parse | question to outcome and optional intent filter | `src/explain.py` |
| 5 Compare | treatment vs control, tests, effect sizes, 30-call floor | `src/compare.py` |
| 6 Attribute | leave-one-turn-out ablation on a lightweight classifier | `src/explain.py` |
| 7 Synthesize | LLM writes from the evidence only | `src/explain.py` |
| Task 2 | `Session`: refine, drill down, new topic | `src/explain.py` |

The offline stages (0 to 2) only need to be re-run to rebuild the processed tables; see the docstring at the top of each
file for its arguments. A vector index (Stage 3 in the original design) is not part of this submission.

## Run

Interactive chat:

```bash
python3 src/explain.py
```

Example conversation:

```
> Why are escalations happening?          # Task 1, stages 4 to 7
> What about <intent> calls only?         # refine: filter merged, stages re-run
> Show me more examples                   # drill down: no recompute
> Now why do customers ask for refunds?   # new topic: resets everything
```

Scripted demo for a recording:

```bash
python3 scripts/demo.py            # press Enter between questions
python3 scripts/demo.py --auto     # no pauses
```

## Reproduce the query dataset, outputs and metrics

```bash
python3 scripts/run_submission.py     # runs queries.txt plus per-intent queries and 3 scripted sessions -> outputs/
python3 scripts/evaluate.py           # query_dataset.csv, eval_metrics.json, eval_report.md
python3 scripts/make_report.py         # report/technical_report.pdf with the measured numbers
node scripts/make_deck.js                # deck/presentation.pptx (needs: npm install)
```

Optional: put hand-labelled label-quality results in `outputs/label_accuracy.json`
(`{"n": 100, "accuracy": 0.87, "macro_f1": 0.84, "note": "..."}`) and they appear in the report.

`outputs/summary.json` records which LLM wrote the answers (`llm_provider`).

## Metrics

| Metric | What it checks |
|---|---|
| Behaviour accuracy | answers, abstains (under 30 calls) or declines as expected |
| Outcome parsing accuracy | question mapped to the right outcome, on hand-labelled queries |
| Evidence grounding | cited turn text exists in the corpus |
| Citation validity | `[call, turn]` references in answers exist in the evidence given to the writer |
| Follow-up routing | refine and new topic recompute, drill down does not |
| Attribution validity | top ablated turn beats a random turn (one-sided Wilcoxon) |

## Limitations

- Outcome labels are silver labels, not ground truth; findings are statistical associations, not proven causes.
- Attribution explains a lightweight proxy classifier.
- Query parsing is keyword-based. A question naming several outcomes is not answered; the user is asked to pick one.
- Drill-down serves more cited turns; it does not yet re-slice the group by another dimension such as agent.

## Tests

```bash
python3 -m pytest tests
```
<img width="500" height="auto" alt="image" src="https://github.com/user-attachments/assets/5b2b6ce4-924f-4f47-988a-29a65037d408" />
