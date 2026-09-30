"""
Outcome labeling via Groq's Batch API (OpenAI-compatible), replacing both the
earlier bart-large-mnli zero-shot signal and the Anthropic-based version
(kept for reference as label_with_claude.py; use this one if you don't have
an Anthropic API key).

Same methodology as the Claude version, same fixes already applied:

  1. Reads the FULL conversation per call, not just reason_for_call -- the
     zero-shot model missed signals that only appear in the actual dialogue
     (e.g. a customer naming a competitor mid-conversation).
  2. The prompt directly encodes the two traps found during manual review:
     a word appearing (e.g. "escalation") without the event happening, and
     a competitor being named without a real threat to leave.
  3. merge_outcome no longer lets "intent_hit and keyword_hit" override an
     explicit, high-confidence "no" from the model -- fixed after review
     showed this could silently flip a correct judgment to "confirmed".
  4. "Cross-Brand Mentions" / "Competitor comparison" removed from the churn
     INTENT_PRIOR -- manual review showed both intents commonly co-occur
     with calls where the customer stays / merely compares, not real churn.

KNOWN LIMITATION (documented, not fixed): the churn keyword patterns below
had ZERO matches across all 50 hand-reviewed calls, including all 5 genuine
churn cases -- real churn signals here use actual company names ("might
switch to QuickBuy"), not generic phrasing ("cancel my subscription"). Churn
labeling leans almost entirely on the model's full-conversation judgment.

Groq specifics vs. the Anthropic version:
  - OpenAI-compatible /v1/chat/completions body (messages with system role,
    not a separate "system" field), response lives at
    result["response"]["body"]["choices"][0]["message"]["content"].
  - Batch lifecycle is upload file (purpose=batch) -> create batch
    (input_file_id, endpoint, completion_window) -> poll status ->
    download output_file_id content once terminal.
  - Uses plain `requests` calls (matches Groq's own documented pattern)
    instead of the groq python package, to avoid SDK version mismatches.

Three modes:
  --submit   Build the JSONL batch file, upload it, create the batch. Prints a batch ID.
  --status   Check whether a submitted batch has finished.
  --fetch    Download and parse results once status is "completed".

Usage:
  export GROQ_API_KEY=
  python3 src/label_with_groq.py --submit
  python3 src/label_with_groq.py --status --batch-id batch_...
  python3 src/label_with_groq.py --fetch --batch-id batch_...
"""

import argparse
import json
import re
import time
from pathlib import Path

import pandas as pd
import requests

OUTCOMES = ["escalation", "refund", "churn"]

# Reasoning-capable open-weight model on Groq -- matters here since the task
# is nuanced judgment, not just classification. Fall back to
# "llama-3.3-70b-versatile" via --model if gpt-oss-120b isn't on your tier.
DEFAULT_MODEL = "openai/gpt-oss-120b"

GROQ_API_BASE = "https://api.groq.com/openai/v1"

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
    # see module docstring, point 4.
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


def build_jsonl_lines(calls_df: pd.DataFrame, turns_df: pd.DataFrame, model: str) -> list:
    lines = []
    for _, call in calls_df.iterrows():
        conv_text = build_conversation_text(turns_df, call["transcript_id"])
        user_content = f"Intent category: {call['intent']}\n\nFull conversation:\n{conv_text}"
        request_obj = {
            "custom_id": call["transcript_id"],
            "method": "POST",
            "url": "/v1/chat/completions",
            "body": {
                "model": model,
                "max_tokens": 400,
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": user_content},
                ],
                # Best-effort JSON mode; if your chosen model rejects this
                # param, drop it -- parse_claude_json below is a regex
                # fallback either way.
                "response_format": {"type": "json_object"},
            },
        }
        lines.append(json.dumps(request_obj))
    return lines


def parse_model_json(text: str) -> dict:
    """Extract the JSON object from the model's response, tolerating minor
    formatting noise (e.g. if it wraps the JSON in a sentence or code fence)."""
    match = re.search(r"\{.*\}", text, flags=re.DOTALL)
    if not match:
        return None
    try:
        return json.loads(match.group(0))
    except json.JSONDecodeError:
        return None


def merge_outcome(llm_result: dict, intent_hit: bool, keyword_hit: bool) -> str:
    """Same tiered philosophy as before. A confident "no" from the model is
    trusted outright and returns "none" immediately, before the weaker
    intent+keyword corroboration path gets a chance to override it."""
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


def submit_batch(api_key: str, calls_path: str, turns_path: str, model: str, jsonl_path: str) -> str:
    calls_df = pd.read_parquet(calls_path)
    turns_df = pd.read_parquet(turns_path)

    lines = build_jsonl_lines(calls_df, turns_df, model)
    Path(jsonl_path).parent.mkdir(parents=True, exist_ok=True)
    with open(jsonl_path, "w") as f:
        f.write("\n".join(lines) + "\n")

    size_mb = Path(jsonl_path).stat().st_size / (1024 * 1024)
    print(f"Built {len(lines)} requests -> {jsonl_path} ({size_mb:.1f} MB)")
    if size_mb > 100:
        print("WARNING: file exceeds Groq's 100 MB batch limit. Split calls_df in half and submit two batches.")
        return None

    headers = {"Authorization": f"Bearer {api_key}"}

    print("Uploading batch input file...")
    with open(jsonl_path, "rb") as f:
        upload_resp = requests.post(
            f"{GROQ_API_BASE}/files",
            headers=headers,
            files={"file": (Path(jsonl_path).name, f)},
            data={"purpose": "batch"},
        )
    upload_resp.raise_for_status()
    file_id = upload_resp.json()["id"]
    print(f"Uploaded. file_id: {file_id}")

    print("Creating batch...")
    batch_resp = requests.post(
        f"{GROQ_API_BASE}/batches",
        headers={**headers, "Content-Type": "application/json"},
        json={
            "input_file_id": file_id,
            "endpoint": "/v1/chat/completions",
            "completion_window": "24h",
        },
    )
    batch_resp.raise_for_status()
    batch = batch_resp.json()
    batch_id = batch["id"]
    print(f"Batch submitted. ID: {batch_id}")
    print(f"Status: {batch.get('status')}")
    print("Check progress with: python3 src/label_with_groq.py --status --batch-id " + batch_id)
    return batch_id


def check_status(api_key: str, batch_id: str) -> dict:
    headers = {"Authorization": f"Bearer {api_key}"}
    resp = requests.get(f"{GROQ_API_BASE}/batches/{batch_id}", headers=headers)
    resp.raise_for_status()
    batch = resp.json()
    print(f"Batch {batch_id}")
    print(f"Status: {batch.get('status')}")
    for key in ("total", "completed", "failed"):
        if key in batch:
            print(f"  {key}: {batch[key]}")
    return batch


def fetch_results(api_key: str, batch_id: str, calls_path: str, turns_path: str, out_path: str) -> None:
    batch = check_status(api_key, batch_id)
    if batch.get("status") != "completed":
        print(f"Batch not finished yet (status: {batch.get('status')}). Try again later.")
        return

    output_file_id = batch.get("output_file_id")
    if not output_file_id:
        print("No output_file_id on a completed batch -- something's off, check the batch object above.")
        return

    headers = {"Authorization": f"Bearer {api_key}"}
    print(f"Downloading results file {output_file_id}...")
    content_resp = requests.get(f"{GROQ_API_BASE}/files/{output_file_id}/content", headers=headers)
    content_resp.raise_for_status()
    raw_lines = content_resp.text.strip().split("\n")

    calls_df = pd.read_parquet(calls_path)
    turns_df = pd.read_parquet(turns_path)
    conv_lookup = turns_df.groupby("transcript_id")["text"].apply(lambda t: " ".join(t))

    rows = []
    parse_failures = 0

    for raw_line in raw_lines:
        if not raw_line.strip():
            continue
        result = json.loads(raw_line)
        tid = result.get("custom_id")

        response = result.get("response")
        if response is None or response.get("status_code") != 200:
            parse_failures += 1
            continue

        try:
            response_text = response["body"]["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError):
            parse_failures += 1
            continue

        parsed = parse_model_json(response_text)
        if parsed is None:
            parse_failures += 1
            continue

        call_row = calls_df[calls_df["transcript_id"] == tid]
        intent = call_row["intent"].iloc[0] if not call_row.empty else None
        conv_text = conv_lookup.get(tid, "")

        intent_sig = intent_signal(intent)
        keyword_sig = keyword_signal(conv_text)

        row = {"transcript_id": tid}
        any_confirmed = False
        any_weak = False
        for outcome in OUTCOMES:
            llm_result = parsed.get(outcome)
            status = merge_outcome(llm_result, intent_sig[outcome], keyword_sig[outcome])
            row[f"outcome_{outcome}"] = status == "confirmed"
            row[f"status_{outcome}"] = status
            row[f"llm_value_{outcome}"] = llm_result.get("value") if llm_result else None
            row[f"llm_confidence_{outcome}"] = llm_result.get("confidence") if llm_result else None
            row[f"llm_reason_{outcome}"] = llm_result.get("reason") if llm_result else None
            any_confirmed = any_confirmed or (status == "confirmed")
            any_weak = any_weak or (status == "weak")
        row["low_confidence"] = (not any_confirmed) and any_weak
        rows.append(row)

    labeled_df = calls_df.merge(pd.DataFrame(rows), on="transcript_id", how="left")

    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    labeled_df.to_parquet(out_path, index=False)

    print(f"Fetched {len(rows)} results, {parse_failures} failures/skips")
    print(f"Wrote: {out_path}")
    for outcome in OUTCOMES:
        n = labeled_df[f"outcome_{outcome}"].sum()
        print(f"  {outcome}: {n} ({100 * n / len(labeled_df):.1f}%)")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Label outcomes using Groq's Batch API.")
    parser.add_argument("--calls", default="data/processed/calls_normalized.parquet")
    parser.add_argument("--turns", default="data/processed/turns_normalized.parquet")
    parser.add_argument("--out", default="data/processed/calls_labeled_groq.parquet")
    parser.add_argument("--jsonl", default="data/processed/groq_batch_input.jsonl")
    parser.add_argument("--model", default=DEFAULT_MODEL,
                         help="Groq model id. Default: openai/gpt-oss-120b. "
                              "Fallback if unavailable on your tier: llama-3.3-70b-versatile")
    parser.add_argument("--submit", action="store_true")
    parser.add_argument("--status", action="store_true")
    parser.add_argument("--fetch", action="store_true")
    parser.add_argument("--batch-id", default=None)
    args = parser.parse_args()

    import os
    api_key = os.environ.get("GROQ_API_KEY")
    if not api_key:
        parser.error("Set GROQ_API_KEY in your environment first: export GROQ_API_KEY=")

    if args.submit:
        submit_batch(api_key, args.calls, args.turns, args.model, args.jsonl)
    elif args.status:
        if not args.batch_id:
            parser.error("--status requires --batch-id")
        check_status(api_key, args.batch_id)
    elif args.fetch:
        if not args.batch_id:
            parser.error("--fetch requires --batch-id")
        fetch_results(api_key, args.batch_id, args.calls, args.turns, args.out)
    else:
        parser.error("Pass --submit, --status, or --fetch")
