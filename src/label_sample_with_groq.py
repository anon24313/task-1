"""
Labels the 50-call hand-validation sample using Groq's sync chat completions
API (no Batch API -- that requires paid tier, see label_with_groq.py's
history). This is small enough (50 calls) that a simple sequential loop with
conservative pacing stays comfortably inside Groq's free-tier limits.

Rate limiting: gpt-oss-120b's free tier is 8,000 tokens/minute -- with each
request running roughly 1,000-1,500 tokens (full conversation + system
prompt), that caps out at ~6 requests/minute, tighter than the 30 RPM limit.
Paced at one request per 10 seconds to stay safely under both, with
retry-with-backoff on 429. ~50 calls -> roughly 8-9 minutes total, using
~60,000 tokens -- well inside the 200,000/day budget.

Also builds a three-way comparison directly:
  - your manual labels (from hand_validation_sample.csv's manual_* columns,
    wherever filled in -- blanks are just skipped in the agreement math, so
    this works today even with zero manual labels filled, and improves
    automatically as you fill more in)
  - the zero-shot silver labels (from calls_labeled.parquet, whichever run
    you point --zs-labels at)
  - this script's Groq LLM judgments

Same prompt, same merge logic, same INTENT_PRIOR/keyword fixes as
label_with_groq.py -- kept self-contained here rather than imported, to
match how the other labeling scripts in this project are structured.

Usage:
  export GROQ_API_KEY=
  python3 src/label_sample_with_groq.py
  python3 src/label_sample_with_groq.py --zs-labels data/processed/calls_labeled.parquet
"""

import argparse
import json
import os
import re
import time
from pathlib import Path

import pandas as pd
import requests

OUTCOMES = ["escalation", "refund", "churn"]
DEFAULT_MODEL = "openai/gpt-oss-120b"
GROQ_API_BASE = "https://api.groq.com/openai/v1"
SECONDS_BETWEEN_REQUESTS = 10
MAX_RETRIES = 4

INTENT_PRIOR = {
    "Replacement vs Refund": "refund",
    "Product Returns": "refund",
    "Refund Policy": "refund",
    "Refund Delays": "refund",
    "Claims & refunds": "refund",
    "Fee Complaints": "refund",
    "Cancellation Policies": "churn",
    "Churn Prediction": "churn",
    # "Cross-Brand Mentions" / "Competitor comparison" intentionally excluded --
    # manual review showed both commonly co-occur with calls where the
    # customer stays / merely compares, not genuine churn.
    "Service Complaints": "escalation",
    "Fraud Alerts": "escalation",
    "Urgency & Stress": "escalation",
    "Customer trust": "escalation",
}

_KEYWORD_PATTERNS = {
    "escalation": [r"supervisor", r"manager", r"escalat", r"speak to (someone|a human|a person)", r"higher up"],
    "refund": [r"refund", r"money back", r"reimburse", r"chargeback"],
    "churn": [
        r"cancel(l?ing)? my (account|subscription|policy|plan|service)",
        r"switch(ing)? to (a competitor|another)", r"close my account",
        r"might as well cancel", r"thinking about cancel", r"consider(ing)? cancel",
    ],
}
_COMPILED_PATTERNS = {
    o: [re.compile(p, flags=re.IGNORECASE) for p in patterns] for o, patterns in _KEYWORD_PATTERNS.items()
}

SYSTEM_PROMPT = """You are labeling customer service call transcripts for three outcomes: \
escalation, refund, and churn. For each outcome, decide if it genuinely happened or was \
explicitly requested in THIS call, not just discussed abstractly or hinted at.

Watch for these specific traps:
- escalation: only counts if a supervisor/manager actually gets involved or the customer \
explicitly demands one. A word like "escalation" appearing in the dialogue (e.g. an agent \
saying "this could lead to escalations") does NOT count if no actual handoff or demand \
happens. Frustration alone is not escalation.
- refund: only counts if the customer is asking about getting THEIR OWN money back, not a \
general policy question. An agent unprompted offering something the customer never asks \
for or confirms does not count.
- churn: counts if the customer expresses real intent to leave or switch providers, even if \
hedged ("might as well cancel", "considering switching"). Simply mentioning a competitor's \
name or price for comparison, while still continuing with the current company, does NOT \
count on its own -- there must be actual expressed leave-intent.

Respond with ONLY a JSON object, no other text, in exactly this shape:
{"escalation": {"value": true or false, "confidence": "high" or "medium" or "low", "reason": "one short sentence"}, \
"refund": {...same shape...}, "churn": {...same shape...}}
"""


def keyword_signal(conversation_text: str) -> dict:
    text_lower = conversation_text.lower()
    return {o: any(p.search(text_lower) for p in patterns) for o, patterns in _COMPILED_PATTERNS.items()}


def intent_signal(intent: str) -> dict:
    prior = INTENT_PRIOR.get(intent)
    return {o: (o == prior) for o in OUTCOMES}


def build_conversation_text(turns_df: pd.DataFrame, transcript_id: str) -> str:
    call_turns = turns_df[turns_df["transcript_id"] == transcript_id].sort_values("turn_id")
    speaker_col = "speaker_role" if "speaker_role" in call_turns.columns else "speaker"
    return "\n".join(f"[{getattr(r, speaker_col)}] {r.text}" for r in call_turns.itertuples())


def parse_model_json(text: str) -> dict:
    match = re.search(r"\{.*\}", text, flags=re.DOTALL)
    if not match:
        return None
    try:
        return json.loads(match.group(0))
    except json.JSONDecodeError:
        return None


def merge_outcome(llm_result: dict, intent_hit: bool, keyword_hit: bool) -> str:
    """A confident 'no' from the model is trusted outright, before weaker
    intent+keyword corroboration gets a chance to override it."""
    if llm_result is None:
        return "weak" if (intent_hit or keyword_hit) else "none"

    value = llm_result.get("value", False)
    confidence = llm_result.get("confidence", "low")

    if not value and confidence == "high":
        return "none"
    if value and confidence == "high":
        return "confirmed"
    if value and confidence == "medium" and (intent_hit or keyword_hit):
        return "confirmed"
    if intent_hit and keyword_hit:
        return "confirmed"
    if value or intent_hit or keyword_hit:
        return "weak"
    return "none"


def call_groq_sync(api_key: str, model: str, conv_text: str, intent: str) -> dict:
    """One sync chat-completions call, with retry-with-backoff on 429."""
    user_content = f"Intent category: {intent}\n\nFull conversation:\n{conv_text}"
    body = {
        "model": model,
        "max_tokens": 400,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ],
        "response_format": {"type": "json_object"},
    }
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

    for attempt in range(MAX_RETRIES):
        resp = requests.post(f"{GROQ_API_BASE}/chat/completions", headers=headers, json=body)
        if resp.status_code == 200:
            return resp.json()
        if resp.status_code == 429:
            wait = int(resp.headers.get("Retry-After", 15 * (attempt + 1)))
            print(f"    rate-limited, waiting {wait}s (attempt {attempt + 1}/{MAX_RETRIES})...")
            time.sleep(wait)
            continue
        # Any other error: surface it, don't silently retry forever
        print(f"    error {resp.status_code}: {resp.text[:200]}")
        return None
    print("    gave up after max retries")
    return None


def run(api_key: str, model: str, sample_csv: str, calls_path: str, turns_path: str, out_path: str) -> pd.DataFrame:
    sample_df = pd.read_csv(sample_csv)
    calls_df = pd.read_parquet(calls_path)
    turns_df = pd.read_parquet(turns_path)

    rows = []
    total = len(sample_df)
    for i, sample_row in sample_df.iterrows():
        tid = sample_row["transcript_id"]
        call_row = calls_df[calls_df["transcript_id"] == tid]
        intent = call_row["intent"].iloc[0] if not call_row.empty else sample_row.get("intent")
        conv_text = build_conversation_text(turns_df, tid)

        print(f"[{i + 1}/{total}] {tid} ({intent})...")
        response = call_groq_sync(api_key, model, conv_text, intent)

        parsed = None
        if response is not None:
            try:
                content = response["choices"][0]["message"]["content"]
                parsed = parse_model_json(content)
            except (KeyError, IndexError, TypeError):
                parsed = None

        intent_sig = intent_signal(intent)
        keyword_sig = keyword_signal(conv_text)

        row = {"transcript_id": tid, "intent": intent}
        for outcome in OUTCOMES:
            llm_result = parsed.get(outcome) if parsed else None
            status = merge_outcome(llm_result, intent_sig[outcome], keyword_sig[outcome])
            row[f"groq_outcome_{outcome}"] = status == "confirmed"
            row[f"groq_status_{outcome}"] = status
            row[f"groq_confidence_{outcome}"] = llm_result.get("confidence") if llm_result else None
            row[f"groq_reason_{outcome}"] = llm_result.get("reason") if llm_result else None
        rows.append(row)

        if i < total - 1:
            time.sleep(SECONDS_BETWEEN_REQUESTS)

    groq_df = pd.DataFrame(rows)

    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    groq_df.to_csv(out_path, index=False)
    print(f"\nWrote Groq judgments -> {out_path}")

    return groq_df


def three_way_comparison(sample_csv: str, groq_df: pd.DataFrame, zs_labels_path: str) -> None:
    manual_df = pd.read_csv(sample_csv)
    zs_df = pd.read_parquet(zs_labels_path)[["transcript_id"] + [f"outcome_{o}" for o in OUTCOMES]]
    zs_df = zs_df.rename(columns={f"outcome_{o}": f"zs_outcome_{o}" for o in OUTCOMES})

    merged = manual_df.merge(zs_df, on="transcript_id", how="left").merge(groq_df, on="transcript_id", how="left")

    print("\n=== Three-way comparison (blank manual labels are skipped, not counted as disagreement) ===")
    for outcome in OUTCOMES:
        manual_col = merged[f"manual_{outcome}"]
        has_manual = manual_col.notna()
        n_manual = has_manual.sum()

        print(f"\n{outcome} ({n_manual}/{len(merged)} manually labeled so far):")

        zs_col = merged[f"zs_outcome_{outcome}"].astype(bool)
        groq_col = merged[f"groq_outcome_{outcome}"].astype(bool)

        zs_vs_groq_agree = (zs_col == groq_col).mean()
        print(f"  zero-shot vs Groq agreement (all {len(merged)} calls): {100 * zs_vs_groq_agree:.1f}%")

        if n_manual > 0:
            manual_bool = manual_col[has_manual].astype(str).str.strip().str.lower().map(
                {"true": True, "false": False}
            )
            zs_vs_manual = (zs_col[has_manual].values == manual_bool.values).mean()
            groq_vs_manual = (groq_col[has_manual].values == manual_bool.values).mean()
            print(f"  zero-shot vs manual agreement: {100 * zs_vs_manual:.1f}%")
            print(f"  Groq vs manual agreement:      {100 * groq_vs_manual:.1f}%")
        else:
            print("  (no manual labels filled in yet -- fill in hand_validation_sample.csv to see this)")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Label the 50-call sample with Groq (sync) and compare three ways.")
    parser.add_argument("--sample", default="data/processed/hand_validation_sample.csv")
    parser.add_argument("--calls", default="data/processed/calls_normalized.parquet")
    parser.add_argument("--turns", default="data/processed/turns_normalized.parquet")
    parser.add_argument("--zs-labels", default="data/processed/calls_labeled.parquet",
                         help="Path to the zero-shot silver labels to compare against.")
    parser.add_argument("--out", default="data/processed/calls_labeled_groq_sample.csv")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    args = parser.parse_args()

    api_key = os.environ.get("GROQ_API_KEY")
    if not api_key:
        parser.error("Set GROQ_API_KEY in your environment first: export GROQ_API_KEY=")

    groq_df = run(api_key, args.model, args.sample, args.calls, args.turns, args.out)
    three_way_comparison(args.sample, groq_df, args.zs_labels)
